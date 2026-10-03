"""probe_contest_transport.py — LIVE EVIDENCE for Stage 2B.

Drives the REAL authority boundary and shows what actually crosses it.  This is
the live-evidence companion to ``verify_contest_transport_contract.py`` (the
static contract gate) and ``tmp_run_stage2b_regression.py`` (the whole engine,
with the boundary object captured).

WHAT IT SHOWS, ON THE REAL BOUNDARY
-----------------------------------
  [A] ONE PAYLOAD PER DECLARATION — the authority assembles ONE first-class
      Contest, at the declaration, on the manager channel and on every engage
      channel, and publishes it as live simulation state.
  [B] THE OBJECT CROSSES THE BOUNDARY — what the authority hands the engine's
      single TES entry is a Contest, not a defender, and the winning defender it
      carries IS the body that was admitted.
  [C] TES CONSUMES ONLY THE WINNER — the same declaration is run twice: once
      normally, once with EVERY advisory fact of the payload adversarially
      corrupted at build time.  The decision, the predicate-call order, the
      carrier state and the winner handed to TES must be IDENTICAL, because TES
      reads only what the payload carries.
  [D] THE RECORD IS DERIVED — in that same corrupted run the historical record
      FOLLOWS the corrupted payload.  If the record were assembled beside the
      payload rather than projected from it, it could not.
  [E] THE FAILED FRAME HAS NO PAYLOAD — a frame that declares no contest
      publishes no Contest, and its record is still produced.
  [F] NOT GATED — with the RECORDER switched off, the payload is still
      assembled and still crosses.  The recorder's switch cannot remove the
      object TES is handed.

Run:  python probe_contest_transport.py
"""

from __future__ import annotations

import dataclasses
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

# The 2A probe owns the constructed-body helpers; reusing them keeps ONE
# definition of "a real body carrying the stamps the authority reads".
from probe_contest_preservation import StubOPE, make_bc, make_body, make_world

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    """Record and print one probe result."""
    ok = bool(ok)
    (PASSED if ok else FAILED).append(name)
    print("  %s  %-54s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def info(name, detail):
    """Print one informational measurement."""
    print("  INFO  %-54s %s" % (name, detail))


# ─────────────────────────────────────────────────────────────────────
# THE ONE DECLARATION, WITH ITS POPULATION
# ─────────────────────────────────────────────────────────────────────

def _line():
    """A three-body in-radius line: a MISS, the winner, and a DISCARDED body.

    The third body sits inside the contact radius and after the winner in scan
    order, so the decider's short-circuit never reaches it.  It is the body
    Stage 1B's boundary destroyed and Stage 2A's record holds — and Stage 2B
    now carries it IN the payload that crosses.
    """
    return [make_body(10, 59.6, 35.0), make_body(11, 59.7, 35.0),
            make_body(12, 59.8, 35.0)]


def declare_manager(preserve=True):
    """One manager-channel declaration.  Returns (world, ope, bc, decision)."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    os.environ[cp.PRESERVATION_ENV_VAR] = "1" if preserve else "0"

    bc = make_bc(vx=8.0)
    line = _line()
    world = make_world(501, line)
    ope = StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    return world, ope, bc, decision


def declare_engage(channel, preserve=True):
    """One engage-channel declaration.  Returns (world, ope, bc, winner)."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp

    os.environ[cp.PRESERVATION_ENV_VAR] = "1" if preserve else "0"

    bc = make_bc()
    # The admitted body sits OUTSIDE the decider's 1.5 m contact radius: the
    # engage gate admits on a wider radius, which is exactly why the payload —
    # not a radius test — is what carries the declared geometry.
    admitted = make_body(20, 63.0, 35.0)
    near = make_body(11, 59.7, 35.0)
    line = [admitted, near]
    world = make_world(502, line)
    ope = StubOPE({})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    ca.enter_tes(world, ope, bc, admitted, channel, defenders=line)
    return world, ope, bc, admitted


# ─────────────────────────────────────────────────────────────────────
# [A] ONE PAYLOAD PER DECLARATION
# ─────────────────────────────────────────────────────────────────────

def section_one_payload():
    """The authority assembles and publishes ONE Contest per declaration."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest as ct

    print("\n[A] ONE PAYLOAD PER DECLARATION (assembled at the declaration)")

    world, ope, bc, decision = declare_manager()
    payload = ct.live_contest(world)

    check("A_the_live_contest_is_a_first_class_object",
          payload is not None and isinstance(payload, ct.Contest),
          "type=%s" % type(payload).__name__)

    if payload is None:
        return

    info("the declared payload", ct.format_contest_report(world))
    check("A_the_payload_names_the_declared_winner",
          payload.winner_id == decision.winner_id,
          "payload winner=%s decision winner=%s"
          % (payload.winner_id, decision.winner_id))
    check("A_the_payload_carries_the_winning_DEFENDER",
          ct.winner_of(payload) is decision.winner,
          "the object in the payload IS the body the entry will be handed")
    check("A_the_payload_carries_the_declaration_frame",
          payload.frame == int(getattr(world, "frame_count", 0) or 0),
          "frame=%s" % payload.frame)
    check("A_the_payload_carries_the_authority_sequence",
          payload.seq == decision.seq, "seq=%s" % payload.seq)
    check("A_the_payload_names_its_admission_channel",
          payload.channel == ca.CHANNEL_MANAGER_TRIGGER,
          "channel=%s" % payload.channel)
    check("A_the_payload_carries_the_declared_radius",
          payload.radius_m == ca.contact_radius_m(),
          "radius_m=%s (the radius owner, not a tuner)" % payload.radius_m)

    # THE DECLARED SETS ARE THE DECLARATION'S OWN GEOMETRY.
    check("A_the_evaluated_set_is_the_declaration's",
          payload.evaluated_ids == (10, 11),
          "evaluated_ids=%s" % (payload.evaluated_ids,))
    check("A_the_in_radius_set_holds_the_body_the_scan_never_reached",
          payload.in_radius_ids == (10, 11, 12),
          "in_radius_ids=%s" % (payload.in_radius_ids,))
    check("A_the_two_sets_are_NOT_the_same_thing",
          payload.evaluated_ids != payload.in_radius_ids,
          "the boundary's whole loss was conflating these: evaluated=%s "
          "in_radius=%s" % (payload.evaluated_ids, payload.in_radius_ids))

    # EVERY DECLARED ENGAGE CHANNEL ASSEMBLES ONE TOO.
    channels = (
        ca.CHANNEL_STRUCTURED_ENGAGE, ca.CHANNEL_UNIFIED_ENGAGE,
        ca.CHANNEL_PURSUIT_ENGAGE, ca.CHANNEL_LVL1_CHASE_ENGAGE,
    )
    built = []
    for channel in channels:
        e_world, _e_ope, _e_bc, admitted = declare_engage(channel)
        e_payload = ct.live_contest(e_world)
        if (e_payload is not None
                and e_payload.channel == channel
                and e_payload.winner is admitted):
            built.append(channel)
    check("A_every_declared_engage_channel_assembles_a_payload",
          len(built) == len(channels),
          "channels=%s" % built)


# ─────────────────────────────────────────────────────────────────────
# [B] THE OBJECT CROSSES THE BOUNDARY
# ─────────────────────────────────────────────────────────────────────

def section_the_object_crosses():
    """What the authority hands the TES entry is a Contest."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest as ct

    print("\n[B] THE OBJECT CROSSES THE BOUNDARY (not a bare defender)")

    # ── the manager hand-over ──
    world, ope, bc, decision = declare_manager()
    payload = ct.live_contest(world)
    ca.enter_tes(world, ope, bc, decision.winner, ca.CHANNEL_MANAGER_TRIGGER,
                 already_decided=True)

    handed = ope.start_calls
    info("what the entry received", "start_calls=%s" % (handed,))
    check("B_the_manager_hand_over_reached_the_entry", len(handed) == 1,
          "calls=%d" % len(handed))
    check("B_the_manager_hand_over_reused_THE_declared_payload",
          payload is not None and bool(handed)
          and handed[0] == ct.winner_of(payload).player_id,
          "the entry identified the payload's own winner: %s" % (handed,))

    # The object-level proof, made explicit: wrap the entry so the ARGUMENT is
    # captured, not the identifier it names.
    captured = []
    original = ope._start_tackle

    def _capture(bc_arg, d_arg):
        captured.append(d_arg)
        return original(bc_arg, d_arg)

    ope._start_tackle = _capture
    try:
        ca.enter_tes(world, ope, bc, decision.winner,
                     ca.CHANNEL_MANAGER_TRIGGER, already_decided=True)
    finally:
        ope._start_tackle = original

    check("B_what_crosses_the_boundary_IS_a_Contest",
          len(captured) == 1 and isinstance(captured[0], ct.Contest),
          "received type=%s" % (type(captured[0]).__name__
                                if captured else "nothing"))
    if captured and isinstance(captured[0], ct.Contest):
        check("B_the_crossing_object_carries_the_admitted_winner",
              ct.winner_of(captured[0]) is decision.winner,
              "winner_of(payload) is the admitted body")
        check("B_the_crossing_object_also_names_every_declared_fact",
              all(getattr(captured[0], name, None) is not None
                  for name in ct.declared_fact_names())
              if hasattr(ct, "declared_fact_names")
              else bool(captured[0].contest_id and captured[0].channel),
              "%s" % ct.format_contest_report(world))
# ─────────────────────────────────────────────────────────────────────
# [C]/[D] THE SAME DECLARATION, WITH THE PAYLOAD'S FACTS CORRUPTED
# ─────────────────────────────────────────────────────────────────────

def _capture_run(scramble=None):
    """Run ONE manager-channel declaration, optionally corrupting the payload.

    ``scramble`` is applied to the payload the authority builds, INSIDE the
    declaration, so the corrupted object is the one every consumer sees.  The
    declaration's own decision has already been taken and published by then, so
    the experiment cannot alter it.
    """
    from game.defence.defence_lvl_1 import contest as ct
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    original = ct.build_contest
    if scramble is not None:
        def _scrambled(**kwargs):
            return scramble(original(**kwargs))
        ct.build_contest = _scrambled

    try:
        bc = make_bc(vx=8.0)
        line = _line()
        world = make_world(601, line)
        ope = StubOPE({10: "MISS", 11: "CONTACT"})
        ca.reset_contact_authority(world)
        cp.reset_preservation(world)
        decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
        payload = ct.live_contest(world)
        record = cp.last_record(world)
        ca.enter_tes(world, ope, bc, decision.winner,
                     ca.CHANNEL_MANAGER_TRIGGER, already_decided=True)
        return {
            "decision": decision, "payload": payload, "record": record,
            "calls": tuple(ope.calls), "start": tuple(ope.start_calls),
            "carrier": (bc.x, bc.y, bc.vx, bc.vy),
        }
    finally:
        ct.build_contest = original


def _corrupt(payload):
    """A payload with EVERY advisory fact changed and the winner UNTOUCHED.

    The declared sets are emptied, so the record's own membership views have to
    move if — and only if — they are sourced from the declaration.
    """
    return dataclasses.replace(
        payload,
        contest_id="corrupted",
        frame=payload.frame + 777,
        seq=payload.seq + 999,
        phase="CORRUPTED_PHASE",
        channel="corrupted_channel",
        outcome="CORRUPTED_OUTCOME",
        reason="corrupted_reason",
        radius_m=payload.radius_m + 9.0,
        arm_grab_damping=not payload.arm_grab_damping,
        glancing_displacement=(1.0 if payload.glancing_displacement is None
                               else None),
        evaluated_ids=(),
        in_radius_ids=(),
    )


def section_only_the_winner_is_consumed():
    """TES reads one field: corrupting every other field changes nothing."""
    from game.defence.defence_lvl_1 import contest as ct

    print("\n[C] TES CONSUMES ONLY THE WINNER (every other fact corrupted)")

    honest = _capture_run()
    corrupt = _capture_run(_corrupt)

    info("honest payload",
         "winner=%s channel=%s frame=%s seq=%s"
         % (honest["payload"].winner_id, honest["payload"].channel,
            honest["payload"].frame, honest["payload"].seq))
    info("corrupted payload",
         "winner=%s channel=%s frame=%s seq=%s"
         % (corrupt["payload"].winner_id, corrupt["payload"].channel,
            corrupt["payload"].frame, corrupt["payload"].seq))

    check("C_the_corruption_reached_the_boundary",
          corrupt["payload"] is not None
          and corrupt["payload"].channel == "corrupted_channel",
          "channel=%s" % (corrupt["payload"].channel
                          if corrupt["payload"] else None))

    # THE DECISION IS UNTOUCHED.  It was taken before the payload existed.
    fields = ("outcome", "reason", "winner_id", "seq", "phase",
              "arm_grab_damping", "glancing_displacement")
    differing = {name: (getattr(honest["decision"], name),
                        getattr(corrupt["decision"], name))
                 for name in fields
                 if getattr(honest["decision"], name)
                 != getattr(corrupt["decision"], name)}
    check("C_the_decision_is_IDENTICAL_under_a_corrupted_payload",
          not differing, "differences=%s" % (differing or "none"))

    check("C_the_predicate_call_order_is_IDENTICAL",
          honest["calls"] == corrupt["calls"],
          "calls=%s" % (corrupt["calls"],))
    check("C_the_carrier_state_is_IDENTICAL",
          honest["carrier"] == corrupt["carrier"],
          "carrier=%s" % (corrupt["carrier"],))

    # THE ONE FIELD THAT CROSSES IS STILL THE WINNER.
    check("C_TES_still_receives_the_SAME_winning_defender",
          honest["start"] == corrupt["start"]
          and bool(corrupt["start"]),
          "handed over: %s" % (corrupt["start"],))
    check("C_the_corrupted_payload_still_carries_the_ADMITTED_body",
          ct.winner_of(corrupt["payload"]) is corrupt["decision"].winner
          and ct.winner_of(honest["payload"]) is honest["decision"].winner,
          "the adversary could not move the winner: each run still hands TES "
          "the body its own declaration admitted")


def section_the_record_is_derived():
    """The record FOLLOWS the declaration it was handed."""
    from game.defence.defence_lvl_1 import contest as ct

    print("\n[D] THE RECORD IS DERIVED (it follows the declaration)")

    honest = _capture_run()
    corrupt = _capture_run(_corrupt)

    h_record = honest["record"]
    c_record = corrupt["record"]
    c_payload = corrupt["payload"]

    if h_record is None or c_record is None or c_payload is None:
        check("D_both_runs_published_a_record", False,
              "honest=%s corrupt=%s"
              % (h_record is not None, c_record is not None))
        return

    # THE HONEST RUN: the payload and the record are the SAME declaration.
    info("honest record",
         "#%d frame=%d seq=%d %s channel=%s winner=%s evaluated=%d "
         "in_radius=%d" % (h_record.record_index, h_record.frame,
                           h_record.seq, h_record.outcome, h_record.channel,
                           h_record.winner_id, h_record.evaluated_count,
                           h_record.in_radius_count))
    check("D_the_honest_record_matches_the_honest_payload",
          h_record.frame == honest["payload"].frame
          and h_record.seq == honest["payload"].seq
          and h_record.channel == honest["payload"].channel
          and h_record.outcome == honest["payload"].outcome
          and h_record.reason == honest["payload"].reason
          and h_record.radius_m == honest["payload"].radius_m,
          "frame/seq/channel/outcome/reason/radius all copied")
    check("D_the_honest_record_sets_match_the_payload_sets",
          h_record.evaluated_ids == honest["payload"].evaluated_ids
          and h_record.in_radius_ids == honest["payload"].in_radius_ids,
          "evaluated=%s in_radius=%s"
          % (h_record.evaluated_ids, h_record.in_radius_ids))

    # THE CORRUPTED RUN: every one of those facts FOLLOWED the payload.
    check("D_the_record_frame_followed_the_payload",
          c_record.frame == c_payload.frame,
          "record frame=%s payload frame=%s"
          % (c_record.frame, c_payload.frame))
    check("D_the_record_sequence_followed_the_payload",
          c_record.seq == c_payload.seq,
          "record seq=%s payload seq=%s" % (c_record.seq, c_payload.seq))
    check("D_the_record_channel_and_admission_followed_the_payload",
          c_record.channel == c_payload.channel
          and c_record.admission_predicate == "UNDECLARED_CHANNEL",
          "channel=%s predicate=%s (an undeclared channel is reported as "
          "undeclared rather than guessed)"
          % (c_record.channel, c_record.admission_predicate))
    check("D_the_record_outcome_and_reason_followed_the_payload",
          c_record.outcome == c_payload.outcome
          and c_record.reason == c_payload.reason,
          "%s / %s" % (c_record.outcome, c_record.reason))
    check("D_the_record_phase_followed_the_payload",
          c_record.phase == c_payload.phase,
          "phase=%s" % c_record.phase)
    check("D_the_record_radius_followed_the_payload",
          c_record.radius_m == c_payload.radius_m,
          "radius_m=%s" % c_record.radius_m)
    check("D_the_record_effects_followed_the_payload",
          bool(c_record.arm_grab_damping) == bool(c_payload.arm_grab_damping)
          and c_record.glancing_displacement == c_payload.glancing_displacement,
          "arm_grab=%s glancing=%s"
          % (c_record.arm_grab_damping, c_record.glancing_displacement))

    # THE SETS: a payload that declares NOTHING evaluated and NOTHING in radius
    # produces a record that reports exactly that.  This is the check that
    # separates a projection from a parallel assembly — if the record were
    # forming its own opinion about membership, it would still say 2 and 3.
    check("D_the_record_evaluated_view_followed_the_payload",
          c_record.evaluated_ids == () and c_record.evaluated_count == 0,
          "evaluated=%s count=%d (the payload declared none)"
          % (c_record.evaluated_ids, c_record.evaluated_count))
    check("D_the_record_in_radius_view_followed_the_payload",
          c_record.in_radius_ids == () and c_record.in_radius_count == 0,
          "in_radius=%s count=%d (the payload declared none)"
          % (c_record.in_radius_ids, c_record.in_radius_count))
    info("the two runs' membership",
         "honest evaluated=%s in_radius=%s | corrupted evaluated=%s "
         "in_radius=%s"
         % (h_record.evaluated_ids, h_record.in_radius_ids,
            c_record.evaluated_ids, c_record.in_radius_ids))


# ─────────────────────────────────────────────────────────────────────
# [E] THE FAILED FRAME HAS NO PAYLOAD
# ─────────────────────────────────────────────────────────────────────

def section_the_failed_frame():
    """No contest declared, so no Contest published — and still a record."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest as ct
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[E] THE FAILED FRAME HAS NO PAYLOAD")

    bc = make_bc(vx=20.0)
    line = [make_body(11, 59.7, 35.0)]
    world = make_world(700, line)
    ope = StubOPE({11: "ARM_GRAB"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())

    check("E_the_frame_declared_NO_contest",
          decision.winner_id is None
          and decision.outcome is ca.ContactOutcome.CONTACT_FAILED,
          "outcome=%s winner=%s"
          % (decision.outcome.value, decision.winner_id))
    check("E_no_payload_was_published",
          ct.live_contest(world) is None,
          "live_contest=%r" % (ct.live_contest(world),))

    record = cp.last_record(world)
    check("E_the_failure_is_STILL_recorded",
          record is not None
          and record.outcome == ca.ContactOutcome.CONTACT_FAILED.value,
          "record outcome=%s" % (None if record is None else record.outcome))
    check("E_the_failed_record_is_derived_from_the_decision_alone",
          record is not None
          and record.reason == ca.ContactFailureReason.SCORE_ARM_GRAB.value
          and record.seq == decision.seq,
          "reason=%s seq=%s"
          % (None if record is None else record.reason,
             None if record is None else record.seq))


# ─────────────────────────────────────────────────────────────────────
# [F] THE TRANSPORT IS NOT GATED
# ─────────────────────────────────────────────────────────────────────

def section_not_gated():
    """With the RECORDER off, the payload is still assembled and still lands."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest as ct
    from game.defence.defence_lvl_1 import contest_preservation as cp

    print("\n[F] THE TRANSPORT IS NOT GATED (the recorder's switch)")

    world, ope, bc, decision = declare_manager(preserve=False)
    payload = ct.live_contest(world)

    check("F_the_recorder_was_OFF", not cp.preservation_enabled(),
          "RLM_CONTEST_PRESERVATION=0")
    check("F_the_recorder_published_no_record",
          cp.last_record(world) is None,
          "last_record=%r" % (cp.last_record(world),))
    check("F_the_payload_was_STILL_assembled",
          payload is not None and isinstance(payload, ct.Contest),
          "type=%s" % type(payload).__name__)
    check("F_the_payload_carries_the_winner_whichever_way",
          payload is not None
          and ct.winner_of(payload) is decision.winner,
          "winner_of(payload) is the admitted body")

    # And it still CROSSES.
    captured = []
    original = ope._start_tackle

    def _capture(bc_arg, d_arg):
        captured.append(d_arg)
        return original(bc_arg, d_arg)

    ope._start_tackle = _capture
    try:
        ca.enter_tes(world, ope, bc, decision.winner,
                     ca.CHANNEL_MANAGER_TRIGGER, already_decided=True)
    finally:
        ope._start_tackle = original

    check("F_the_payload_STILL_crossed_the_boundary_with_the_recorder_off",
          len(captured) == 1 and isinstance(captured[0], ct.Contest),
          "the recorder's switch cannot remove the object TES is handed")

    os.environ[cp.PRESERVATION_ENV_VAR] = "1"


# ─────────────────────────────────────────────────────────────────────
# THE PROBE
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section and print the verdict."""
    print("=" * 78)
    print("CONTEST TRANSPORT PROBE (Stage 2B) - live evidence at the boundary:")
    print("a first-class Contest crosses to TES, which reads only its winner.")
    print("=" * 78)

    section_one_payload()
    section_the_object_crosses()
    section_only_the_winner_is_consumed()
    section_the_record_is_derived()
    section_the_failed_frame()
    section_not_gated()

    print("\n" + "=" * 78)
    if FAILED:
        print("PROBE RESULT: %d FAILED - %s" % (len(FAILED), ", ".join(FAILED)))
        print("=" * 78)
        return 1
    print("PROBE RESULT: the contest crosses the boundary as a first-class "
          "object - %d checks." % len(PASSED))
    print("  Contact Authority assembles ONE Contest per declaration on every")
    print("  channel; what reaches the TES entry is that object, and the body")
    print("  it carries is the admitted winner.  Corrupting every OTHER fact in")
    print("  the payload changes no decision, no predicate order and no carrier")
    print("  state - while the historical record follows every corrupted fact,")
    print("  including its membership, which is what 'derived, not assembled")
    print("  beside it' means.  A failed frame declares no payload, and the")
    print("  recorder's switch cannot remove the object TES is handed.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
