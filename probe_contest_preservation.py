"""probe_contest_preservation.py — LIVE EVIDENCE for Stage 2A.

Drives the REAL authority boundary and prints what the preservation layer
actually produced.  This is the live-evidence companion to
``verify_contest_preservation_contract.py`` (the static contract gate) and
``tmp_run_stage2a_regression.py`` (the whole-engine guard).

WHAT IT SHOWS, ON THE REAL BOUNDARY
-----------------------------------
  [A] ONE RECORD PER DECLARATION — the boundary publishes exactly one complete
      record per contest, and none when preservation is switched off.
  [B] THE ANTI-COMPRESSION PROOF — a THREE-body in-radius line where the
      decider forms on the middle body.  Stage 1B destroyed the third body at
      the boundary; the record holds it, named, with its distance.
  [C] THE ENGAGE CHANNEL — an admission whose winner sits OUTSIDE the
      decider's contact radius, attributed to the predicate that admitted it.
  [D] THE DECLARATION GEOMETRY — with a failed ARM_GRAB contact, the record's
      carrier snapshot shows the DAMPED velocity, because the snapshot is
      taken after the decision's own effects.
  [E] NEUTRALITY, ONE FRAME — the SAME starting world, the SAME call, run with
      the recorder OFF and ON: the returned decision, the predicate calls, the
      carrier state and the sequence must be IDENTICAL.  (A single call from an
      identical state is a valid comparison; a whole-rollout diff is not, and
      this probe says so rather than pretending otherwise.)
  [F] THE AUDIT SINK — armed, it writes a record row plus one row per body,
      INCLUDING the body Stage 1B never reached.

Run:  python probe_contest_preservation.py
"""

from __future__ import annotations

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    """Record and print one probe result."""
    ok = bool(ok)
    (PASSED if ok else FAILED).append(name)
    print("  %s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# ─────────────────────────────────────────────────────────────────────
# THE REAL BODIES
# ─────────────────────────────────────────────────────────────────────

class StubOPE:
    """A minimal OPE: scripted verdicts, recording every predicate call."""

    def __init__(self, verdicts=None):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts or {})
        self.calls = []
        self.start_calls = []

    def _check_pre_tackle(self, bc, body):
        pid = getattr(body, "player_id", None)
        self.calls.append(pid)
        return self.verdicts.get(pid, "MISS")

    def _start_tackle(self, bc, body):
        self.start_calls.append(getattr(body, "player_id", None))


def make_body(pid, x, y, role="PRIMARY"):
    """A REAL ``Player`` with the published stamps the authority reads."""
    from player.player import DefenderRole, Player
    body = Player(x=float(x), y=float(y), player_id=int(pid))
    body.tackle_role = role
    body.defender_role = DefenderRole.DEF_LINE_READY
    body.depth_class = "SECONDARY"
    body.pre_tackle_state = None
    body.fullback_role_active = False
    body.can_acquire_tackle = False
    body._pta_corridor = False
    body._pta_corridor_frames = 0
    body._machine_state = None
    body._tackle_engage_guard = False
    body.vx = 0.0
    body.vy = 0.0
    return body


def make_bc(vx=5.0):
    """A REAL carrier."""
    from player.player import Player
    bc = Player(x=60.0, y=35.0, player_id=1)
    bc.vx = float(vx)
    bc.vy = 0.0
    bc.tackle_role = None
    bc.fullback_role_active = False
    return bc


def make_world(frame, team):
    """The minimal world surface the boundary reads."""
    from types import SimpleNamespace
    return SimpleNamespace(
        frame_count=int(frame), ball_carrier=None, defending_team=list(team),
        tes_active=False, ball_owner="BC", players=[],
    )


def dump(record, title):
    """Print every body row of a record."""
    print("    %s" % title)
    print("      %-6s %-8s %-9s %-26s %-9s %s"
          % ("pid", "dist", "in_rad", "disposition", "failed_at", "winner"))
    for row in record.candidates:
        print("      %-6d %-8.2f %-9s %-26s %-9s %s"
              % (row.player_id, row.dist, row.in_radius,
                 row.disposition.value, row.failed_at or "-",
                 "YES" if row.is_winner else ""))
# ─────────────────────────────────────────────────────────────────────
# [A] ONE RECORD PER DECLARATION
# ─────────────────────────────────────────────────────────────────────

def section_one_record():
    """The boundary publishes exactly one complete record per declaration."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[A] ONE RECORD PER DECLARATION")

    bc = make_bc()
    line = [make_body(10, 59.6, 35.0), make_body(11, 59.7, 35.0)]

    # recorder ON
    os.environ[cp.PRESERVATION_ENV_VAR] = "1"
    world = make_world(101, line)
    ope = StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    first = cp.last_record(world)

    check("A_one_record_from_the_boundary", first is not None,
          "a declaration published a record")
    check("A_the_record_names_the_declared_winner",
          first is not None and first.winner_id == decision.winner_id,
          "record winner=%s, decision winner=%s"
          % (None if first is None else first.winner_id, decision.winner_id))
    check("A_the_record_carries_the_authority_sequence",
          first is not None and first.seq == decision.seq,
          "record seq=%s, decision seq=%s"
          % (None if first is None else first.seq, decision.seq))
    check("A_the_record_is_indexed", first is not None
          and first.record_index == 1,
          "index=%s" % (None if first is None else first.record_index))

    # recorder OFF publishes NOTHING and returns the same decision
    os.environ[cp.PRESERVATION_ENV_VAR] = "0"
    world2 = make_world(101, line)
    ope2 = StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world2)
    cp.reset_preservation(world2)
    decision2 = ca.evaluate_contact(world2, ope2, line, bc, DefenceTuners())
    check("A_the_OFF_switch_publishes_no_record",
          cp.last_record(world2) is None,
          "last_record=%s" % cp.last_record(world2))
    check("A_the_OFF_switch_returns_the_same_decision",
          decision2.outcome is decision.outcome
          and decision2.reason is decision.reason
          and decision2.winner_id == decision.winner_id,
          "%s/%s/%s" % (decision2.outcome.value, decision2.reason.value,
                        decision2.winner_id))

    os.environ[cp.PRESERVATION_ENV_VAR] = "1"


# ─────────────────────────────────────────────────────────────────────
# [B] THE ANTI-COMPRESSION PROOF
# ─────────────────────────────────────────────────────────────────────

def section_anti_compression():
    """A three-body in-radius line: the body Stage 1B discarded is held."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[B] THE ANTI-COMPRESSION PROOF (three in-radius bodies)")
    os.environ[cp.PRESERVATION_ENV_VAR] = "1"

    bc = make_bc()
    first = make_body(10, 59.6, 35.0)
    winner = make_body(11, 59.7, 35.0)
    last = make_body(12, 59.8, 35.0)
    line = [first, winner, last]

    world = make_world(102, line)
    ope = StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    record = cp.last_record(world)

    check("B_the_decider_stopped_at_the_middle_body",
          decision.winner_id == 11 and ope.calls == [10, 11],
          "winner=%s, predicate calls=%s (the scan broke at the winner)"
          % (decision.winner_id, ope.calls))

    if record is None:
        check("B_a_record_was_published", False, "none")
        return

    dump(record, "the complete formation state:")
    print("    evaluated=%s  in_radius=%s  discarded=%s"
          % (record.evaluated_ids, record.in_radius_ids,
             record.un_evaluated_in_radius_ids))

    check("B_the_record_holds_ALL_three_in_radius_bodies",
          record.in_radius_ids == (10, 11, 12),
          "in_radius=%s" % (record.in_radius_ids,))
    check("B_and_the_body_the_boundary_discarded_is_named",
          record.un_evaluated_in_radius_ids == (12,),
          "discarded=%s (Stage 1B lost this body)" % (
              record.un_evaluated_in_radius_ids,))
    held = record.row_for(12)
    check("B_the_discarded_body_keeps_its_geometry_and_is_honest_about_why",
          held is not None and held.in_radius
          and held.failed_at is None
          and held.disposition.value == "in_radius_not_evaluated",
          "row 12: dist=%.2f in_radius=%s failed_at=%s disposition=%s"
          % (held.dist, held.in_radius, held.failed_at,
             held.disposition.value))
    check("B_every_row_carries_the_declared_formation_evidence",
          all(row.defender_role and row.depth_class is not None
              for row in record.candidates),
          "%d evidence fields per row" % len(
              record.candidates[0].__dataclass_fields__))


# ─────────────────────────────────────────────────────────────────────
# [C] THE ENGAGE CHANNEL
# ─────────────────────────────────────────────────────────────────────

def section_engage_channel():
    """An admission whose winner is OUTSIDE the decider's contact radius."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp

    print("\n[C] THE ENGAGE CHANNEL (admitted by a predicate outside the "
          "decider)")
    os.environ[cp.PRESERVATION_ENV_VAR] = "1"

    bc = make_bc()
    near = make_body(11, 59.7, 35.0)
    admitted = make_body(20, 63.0, 35.0)   # ~3 m: outside the 1.5 m radius
    line = [admitted, near]

    world = make_world(103, line)
    ope = StubOPE({})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    ca.enter_tes(world, ope, bc, admitted, ca.CHANNEL_STRUCTURED_ENGAGE,
                 defenders=line)
    record = cp.last_record(world)

    check("C_the_engage_channel_entered_TES_on_the_admitted_body",
          ope.start_calls == [20], "start_calls=%s" % ope.start_calls)
    if record is None:
        check("C_a_record_was_published", False, "none")
        return

    dump(record, "the engage admission, per body:")
    check("C_the_admission_is_attributed_to_its_predicate",
          record.channel == ca.CHANNEL_STRUCTURED_ENGAGE
          and record.admission_predicate
          == "game.defence.defence_lvl_1.pretackle_machine.should_engage",
          "%s @ %s" % (record.admission_predicate, record.admission_site))
    check("C_the_winner_is_recorded_OUTSIDE_the_decider_radius",
          record.winner is not None and not record.winner.in_radius,
          "winner=%s in_radius=%s (the engage gate admits wider)"
          % (record.winner_id,
             None if record.winner is None else record.winner.in_radius))
    check("C_the_near_by_body_is_still_held_as_a_candidate",
          record.row_for(11) is not None,
          "in_radius=%s" % (record.in_radius_ids,))
    check("C_the_decider_never_ran_for_this_contest",
          record.decider == cp.DECIDER_CHANNEL,
          "decider=%s" % record.decider)
# ─────────────────────────────────────────────────────────────────────
# [D] THE DECLARATION GEOMETRY
# ─────────────────────────────────────────────────────────────────────

def section_declared_geometry():
    """A failed ARM_GRAB contact: the snapshot shows the DAMPED velocity."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[D] THE DECLARATION GEOMETRY (a failed contact is preserved too)")
    os.environ[cp.PRESERVATION_ENV_VAR] = "1"

    bc = make_bc(vx=20.0)
    line = [make_body(11, 59.7, 35.0)]
    before = bc.vx

    world = make_world(104, line)
    ope = StubOPE({11: "ARM_GRAB"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    record = cp.last_record(world)

    check("D_the_decider_failed_on_this_contact",
          decision.winner_id is None
          and decision.outcome is ca.ContactOutcome.CONTACT_FAILED,
          "outcome=%s winner=%s"
          % (decision.outcome.value, decision.winner_id))
    check("D_the_failed_contact_applied_its_effect",
          bc.vx != before,
          "carrier vx %.4f -> %.4f" % (before, bc.vx))
    if record is None:
        check("D_a_record_was_published_for_the_failure", False, "none")
        return
    check("D_the_failure_IS_preserved",
          record.outcome == ca.ContactOutcome.CONTACT_FAILED.value
          and record.reason == ca.ContactFailureReason.SCORE_ARM_GRAB.value,
          "%s / %s" % (record.outcome, record.reason))
    check("D_the_snapshot_is_taken_AFTER_the_effect",
          abs(record.carrier.vx - bc.vx) < 1e-12
          and abs(record.carrier.vx - before) > 1e-12,
          "record vx=%.4f, live vx=%.4f (post-effect geometry)"
          % (record.carrier.vx, bc.vx))
    check("D_the_record_flags_the_effect_it_applied",
          bool(record.arm_grab_damping) is True,
          "arm_grab_damping=%s" % record.arm_grab_damping)


# ─────────────────────────────────────────────────────────────────────
# [E] NEUTRALITY, ONE FRAME
# ─────────────────────────────────────────────────────────────────────

def run_one_frame(preserve):
    """One declaration from an IDENTICAL starting world; returns what it did."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    os.environ[cp.PRESERVATION_ENV_VAR] = "1" if preserve else "0"

    bc = make_bc(vx=8.0)
    line = [make_body(10, 59.6, 35.0), make_body(11, 59.7, 35.0),
            make_body(12, 59.8, 35.0)]
    world = make_world(105, line)
    ope = StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())

    return {
        "outcome": decision.outcome.value,
        "reason": decision.reason.value,
        "winner": decision.winner_id,
        "seq": decision.seq,
        "phase": str(decision.phase),
        "arm_grab": bool(decision.arm_grab_damping),
        "glancing": decision.glancing_displacement,
        "predicate_calls": tuple(ope.calls),
        "carrier": (bc.x, bc.y, bc.vx, bc.vy),
        "records": len(cp.recent_records()),
    }


def section_one_frame_neutrality():
    """The same call, the same start state: ON and OFF must agree exactly."""
    print("\n[E] NEUTRALITY, ONE FRAME (the same state, the same call)")

    off = run_one_frame(False)
    on = run_one_frame(True)

    keys = [key for key in off if key != "records"]
    differing = {key: (off[key], on[key]) for key in keys
                 if off[key] != on[key]}

    print("    recorder OFF: %s" % {key: off[key] for key in keys})
    print("    recorder ON : %s" % {key: on[key] for key in keys})

    check("E_the_recorder_changed_NO_decision_field", not differing,
          "differences=%s" % (differing or "none"))
    check("E_the_predicate_call_order_is_identical",
          off["predicate_calls"] == on["predicate_calls"],
          "calls=%s" % (on["predicate_calls"],))
    check("E_the_carrier_state_after_the_call_is_identical",
          off["carrier"] == on["carrier"],
          "carrier=%s" % (on["carrier"],))
    check("E_the_ON_run_published_one_record_and_the_OFF_run_none",
          on["records"] == 1 and off["records"] == 0,
          "on=%d off=%d" % (on["records"], off["records"]))


# ─────────────────────────────────────────────────────────────────────
# [F] THE AUDIT SINK
# ─────────────────────────────────────────────────────────────────────

def section_audit_sink():
    """Armed, the sink writes a row per body — including the discarded one."""
    import csv

    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_preservation_audit as cpa
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[F] THE AUDIT SINK (optional, off by default, file-only)")

    path = cpa.audit_sink_path()
    if os.path.exists(path):
        os.remove(path)

    check("F_the_sink_is_OFF_by_default", not cpa.audit_enabled(),
          "RLM_CONTEST_PRESERVATION_AUDIT=%s"
          % os.environ.get(cpa.AUDIT_ENV_VAR, "<unset>"))

    os.environ[cp.PRESERVATION_ENV_VAR] = "1"
    os.environ[cpa.AUDIT_ENV_VAR] = "1"
    check("F_the_sink_arms", cpa.audit_enabled(), "armed")

    try:
        bc = make_bc()
        line = [make_body(10, 59.6, 35.0), make_body(11, 59.7, 35.0),
                make_body(12, 59.8, 35.0)]
        world = make_world(106, line)
        ope = StubOPE({10: "MISS", 11: "CONTACT"})
        ca.reset_contact_authority(world)
        cp.reset_preservation(world)
        ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    finally:
        os.environ.pop(cpa.AUDIT_ENV_VAR, None)

    check("F_the_sink_wrote_its_file", os.path.exists(path), path)

    rows = []
    if os.path.exists(path):
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    record_rows = [row for row in rows if row.get("row_kind") == "record"]
    body_rows = [row for row in rows if row.get("row_kind") == "body"]

    check("F_one_record_row_per_declaration", len(record_rows) == 1,
          "record rows=%d" % len(record_rows))
    check("F_one_body_row_per_examined_body", len(body_rows) == 3,
          "body rows=%d" % len(body_rows))
    discarded = [row for row in body_rows
                 if row.get("disposition") == "in_radius_not_evaluated"]
    check("F_the_body_Stage_1B_discarded_HAS_A_ROW",
          len(discarded) == 1 and discarded[0].get("body_id") == "12",
          "row=%s" % ({key: discarded[0][key]
                       for key in ("body_id", "dist", "in_radius",
                                   "disposition", "never_evaluated")}
                      if discarded else None))
    check("F_every_row_carries_the_declared_columns",
          all(set(row) == set(cpa.AUDIT_COLUMNS) for row in rows),
          "%d columns per row" % len(cpa.AUDIT_COLUMNS))

    if os.path.exists(path):
        os.remove(path)


# ─────────────────────────────────────────────────────────────────────
# THE PROBE
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section and print the verdict."""
    print("=" * 78)
    print("CONTEST PRESERVATION PROBE (Stage 2A) - live evidence at the")
    print("authority boundary: the complete formation state, recorded.")
    print("=" * 78)

    section_one_record()
    section_anti_compression()
    section_engage_channel()
    section_declared_geometry()
    section_one_frame_neutrality()
    section_audit_sink()

    print("\n" + "=" * 78)
    if FAILED:
        print("PROBE RESULT: %d FAILED - %s" % (len(FAILED), ", ".join(FAILED)))
        print("=" * 78)
        return 1
    print("PROBE RESULT: the boundary preserves the COMPLETE contest - %d"
          % len(PASSED))
    print("  checks pass.  One record per declaration carries the winner, the")
    print("  evaluated bodies, the in-radius candidates, the formation evidence,")
    print("  the admission channel and predicate, the frame, the sequence and")
    print("  the outcome; the in-radius body Stage 1B discarded is now NAMED;")
    print("  a failed contact is preserved with the geometry it was declared")
    print("  on; and from an identical state the recorder changes NO decision")
    print("  field at all.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
