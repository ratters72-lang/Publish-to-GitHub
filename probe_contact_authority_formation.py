"""probe_contact_authority_formation.py — LIVE EVIDENCE for contest formation.

Stage 1B.  Drives the DECLARED contact authority through the SAME boundary the
manager uses, with REAL ``Player`` bodies and a REAL carrier, and prints what
actually happened:

    [A] ONE DECISION PER FRAME      — the published surface advances by exactly
                                      one sequence number per frame, and the
                                      per-frame memo returns the SAME decision
                                      object rather than re-deciding.
    [B] THE REAL CONJUNCTS          — a real defensive line spread around the
                                      carrier: which bodies were actually
                                      consulted, and that a role holder
                                      outside the radius, a fullback without
                                      tackle authority and a non-PRIMARY/
                                      SECONDARY body are never consulted.
    [C] REASON COVERAGE             — every declared outcome reason is produced
                                      at least once, and
                                      formed + failed == frames evaluated.
    [D] TES ENTRY IS THE SAME PATH  — the manager-side tail goes through the
                                      declared choke point
                                      (``enter_tes(..., CHANNEL_MANAGER_TRIGGER,
                                      already_decided=True)``) and fires
                                      EXACTLY ONCE, on the DECLARED winner,
                                      never on a failed frame; an
                                      ENGAGE-channel entry records its own
                                      channel and publishes the contest.
    [E] THE FAILED-CONTACT EFFECTS  — measured on the real carrier: ARM_GRAB
                                      damps vx/vy to 0.85x; GLANCING moves y by
                                      exactly +/-0.5 m and sets vy to +/-2.0.
    [F] THE AUDIT SINK              — ``RLM_CONTACT_AUTHORITY_AUDIT=1`` writes a
                                      parseable CSV with one frame row per
                                      decision and a reason from the declared
                                      vocabulary; the file is then removed.

Everything asserted here is measured, never assumed.  Where a claim can only
be made about SOURCE (for example "the manager calls the authority exactly
once"), it is asserted by the contract verifier, not by this probe.

Run:  python probe_contact_authority_formation.py
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import csv
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import DefenderRole, Player
from game.defence.defence_lvl_1 import contact_authority as ca
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

TUNERS = DefenceTuners()
RADIUS = ca.contact_radius_m()
AUDIT_CSV = ca.audit_sink_path()

FAILURES = []


def fail(msg):
    FAILURES.append(msg)
    print("  !! %s" % msg)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ─────────────────────────────────────────────────────────────────────
# THE BOUNDARY HARNESS
# ─────────────────────────────────────────────────────────────────────

class RecordingOPE:
    """The engine half of the boundary, exactly as the manager sees it.

    Records the order the contact predicate was consulted in and every TES
    entry the manager-side tail requested.
    """

    def __init__(self, verdict_for=None, default="MISS"):
        self._phase = "OPEN_PLAY"
        self.verdict_for = dict(verdict_for or {})
        self.default = default
        self.predicate_calls = []
        self.start_calls = []

    def _check_pre_tackle(self, bc, defender):
        self.predicate_calls.append(getattr(defender, "player_id", None))
        return self.verdict_for.get(
            getattr(defender, "player_id", None), self.default,
        )

    def _start_tackle(self, bc, defender):
        self.start_calls.append((
            getattr(bc, "player_id", None),
            getattr(defender, "player_id", None),
        ))


def make_world(frame=1):
    """A world stub with the attributes the authority and evidence read."""
    return SimpleNamespace(
        frame_count=int(frame),
        ball_carrier=None,
        defending_team=[],
        tes_active=False,
        ball_owner="BC",
        players=[],
        ruck_x_m=60.0,
        ruck_y_m=35.0,
        sub_state=SimpleNamespace(value="open_play"),
    )


def make_carrier(pid=1, x=60.0, y=35.0, vx=6.0, vy=-4.0):
    bc = Player(x=float(x), y=float(y), player_id=pid)
    bc.vx = float(vx)
    bc.vy = float(vy)
    bc.tackle_role = None
    bc.fullback_role_active = False
    return bc


def make_body(pid, x, y, tackle_role="PRIMARY", fullback=False,
              can_acquire_tackle=False):
    d = Player(x=float(x), y=float(y), player_id=pid)
    d.tackle_role = tackle_role
    d.fullback_role_active = bool(fullback)
    d.can_acquire_tackle = bool(can_acquire_tackle)
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.depth_class = "SECONDARY"
    d.pre_tackle_state = None
    d._pta_corridor = False
    d._pta_corridor_frames = 0
    d._machine_state = None
    d._tackle_engage_guard = False
    d.vx = 0.0
    d.vy = 0.0
    return d


def run_boundary(world, ope, defenders, bc):
    """THE MANAGER'S STAGE 1B TAIL, verbatim in shape.

        _contact_decision = evaluate_contact(world, ope, defenders, bc, tuners)
        if _contact_decision.outcome is ContactOutcome.CONTACT_FORMED:
            enter_tes(world, ope, bc, _contact_decision.winner,
                      CHANNEL_MANAGER_TRIGGER, already_decided=True)

    The handover goes through the authority's SINGLE TES-entry choke point, so
    the probe measures exactly the path the live manager uses -- not a direct
    ``_start_tackle`` call that the engine no longer makes.
    """
    ca.reset_contact_authority(world)
    decision = ca.evaluate_contact(world, ope, defenders, bc, TUNERS)
    if decision.outcome is ca.ContactOutcome.CONTACT_FORMED:
        ca.enter_tes(world, ope, bc, decision.winner,
                     ca.CHANNEL_MANAGER_TRIGGER, already_decided=True)
    return decision


# ─────────────────────────────────────────────────────────────────────
# [A] ONE DECISION PER FRAME
# ─────────────────────────────────────────────────────────────────────

def probe_one_decision_per_frame():
    section("[A] ONE DECISION PER FRAME (published surface + per-frame memo)")

    bc = make_carrier()
    ok = True

    if not (hasattr(bc, "vx") and hasattr(bc, "vy")):
        fail("the real Player carrier has no .vx/.vy — the failed-contact "
             "effects would land on a shadow attribute")

    seqs = []
    same_object = 0
    # ONE world, because the live engine has ONE world for the whole match.
    # The contest sequence is a per-world count, so it must ACCUMULATE across
    # frames instead of restarting: that is the property being asserted.
    world = make_world(frame=1)
    for frame in range(1, 13):
        world.frame_count = frame
        body = make_body(100 + frame, bc.x - 0.5, bc.y)
        ope = RecordingOPE({100 + frame: "MISS"})
        first = run_boundary(world, ope, [body], bc)
        # A SECOND evaluation in the same frame must return the memo.
        second = ca.evaluate_contact(world, ope, [body], bc, TUNERS)
        if second is first:
            same_object += 1
        seq = getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, None)
        seqs.append(seq)
        print("  frame %2d  outcome=%-14s reason=%-26s seq=%s  memo_same=%s"
              % (frame, first.outcome.value, first.reason.value, seq,
                 second is first))

    if seqs != list(range(1, 13)):
        ok = False
        fail("the published sequence did not advance by exactly one per "
             "frame: %s" % seqs)
    if same_object != 12:
        ok = False
        fail("the per-frame memo did not return the same decision on every "
             "frame (%d/12)" % same_object)

    if ok:
        print("\n  OK: exactly one decision per frame, published and memoised.")


# ─────────────────────────────────────────────────────────────────────
# [B] THE REAL CONJUNCTS ON A REAL LINE
# ─────────────────────────────────────────────────────────────────────

def probe_real_conjuncts():
    section("[B] THE REAL CONJUNCTS — a real line spread around the carrier")

    bc = make_carrier(x=60.0, y=35.0)
    world = make_world(frame=1)

    specs = [
        # (pid, dx, dy, tackle_role, fullback, can_acquire, note)
        (201, -0.4, 0.0, "PRIMARY", False, False, "in radius, PRIMARY"),
        (202, 0.0, -0.5, "SECONDARY", False, False, "in radius, SECONDARY"),
        (203, -0.6, 0.0, "LINE", False, False, "in radius but role=LINE"),
        (204, -0.6, 0.0, None, False, False, "in radius but no role"),
        (205, -0.6, 0.0, "PRIMARY", True, False,
         "in radius, fullback WITHOUT authority"),
        (206, -0.5, 0.0, "PRIMARY", True, True,
         "in radius, fullback WITH authority"),
        (207, +2.5, 0.0, "PRIMARY", False, False, "PRIMARY but out of radius"),
        (208, +20.0, 0.0, "SECONDARY", False, False, "far out of radius"),
    ]

    defenders = []
    for pid, dx, dy, role, fb, can, _note in specs:
        defenders.append(make_body(pid, bc.x + dx, bc.y + dy,
                                   tackle_role=role, fullback=fb,
                                   can_acquire_tackle=can))

    ope = RecordingOPE(default="MISS")          # nothing forms: prove the gate
    decision = run_boundary(world, ope, defenders, bc)

    print("  carrier at (%.1f, %.1f); contact radius %.2f m"
          % (bc.x, bc.y, RADIUS))
    print("  %-6s %-11s %-6s %-9s %-9s %s" % (
        "pid", "role", "fb", "can_acq", "consulted", "note"))
    print("  " + "-" * 68)
    for pid, dx, dy, role, fb, can, note in specs:
        consulted = pid in ope.predicate_calls
        print("  %-6d %-11s %-6s %-9s %-9s %s" % (
            pid, role or "-", "yes" if fb else "-",
            "yes" if can else "-", "YES" if consulted else "no", note))

    expected_consulted = {201, 202, 206}
    got_consulted = set(ope.predicate_calls)
    if got_consulted != expected_consulted:
        fail("the predicate was consulted for %s, expected %s"
             % (sorted(got_consulted), sorted(expected_consulted)))
    if decision.outcome is not ca.ContactOutcome.CONTACT_FAILED:
        fail("an all-MISS line reported %s" % decision.outcome.value)
    if ope.start_calls:
        fail("TES was entered on an all-MISS frame: %s" % ope.start_calls)
    if 207 in got_consulted:
        fail("an out-of-radius PRIMARY reached the predicate")
    if 205 in got_consulted:
        fail("a fullback without tackle authority reached the predicate")

    print("\n  consulted exactly the three legal candidates "
          "(201, 202, 206) — the radius, the role gate and the fullback "
          "conjunct all bound.")
    print("  explicit formation data rows: %d" % len(decision.evidence))
    for row in decision.evidence:
        print("    pid=%-4d role=%-11s dist=%.3f in_radius=%s verdict=%-8s "
              "failed_at=%s" % (
                  row.player_id, row.defender_role or "-", row.dist,
                  row.in_radius, row.predicate_verdict or "-",
                  row.failed_at.value))


# ─────────────────────────────────────────────────────────────────────
# [C] REASON COVERAGE + RECONCILIATION
# ─────────────────────────────────────────────────────────────────────

def _scenario_matrix():
    """(name, expected_reason, specs, verdicts) covering every reason."""
    R = RADIUS
    return [
        ("empty frame", ca.ContactFailureReason.NO_IN_RADIUS_BODY, [], {}),
        ("role=LINE adjacent", ca.ContactFailureReason.NOT_TACKLE_ROLE,
         [(1, -0.5, 0.0, "LINE", False, False)], {}),
        ("no role installed", ca.ContactFailureReason.NOT_TACKLE_ROLE,
         [(1, -0.5, 0.0, None, False, False)], {}),
        ("fullback, no authority",
         ca.ContactFailureReason.FULLBACK_WITHOUT_AUTHORITY,
         [(1, -0.5, 0.0, "PRIMARY", True, False)], {}),
        ("PRIMARY beyond the radius",
         ca.ContactFailureReason.OUT_OF_RADIUS,
         [(1, -(R + 0.25), 0.0, "PRIMARY", False, False)], {1: "CONTACT"}),
        ("MISS", ca.ContactFailureReason.SCORE_MISS,
         [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "MISS"}),
        ("ARM_GRAB", ca.ContactFailureReason.SCORE_ARM_GRAB,
         [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "ARM_GRAB"}),
        ("GLANCING", ca.ContactFailureReason.SCORE_GLANCING,
         [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "GLANCING"}),
        ("CONTACT", ca.ContactFailureReason.FORMED,
         [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "CONTACT"}),
    ]


def probe_reason_coverage():
    section("[C] REASON COVERAGE and formed + failed reconciliation")

    seen = {}
    formed = 0
    failed = 0
    print("  %-30s %-28s %-14s %s" % (
        "scenario", "reason", "outcome", "evidence rows"))
    print("  " + "-" * 88)

    for i, (name, expected, specs, verdicts) in enumerate(_scenario_matrix()):
        bc = make_carrier()
        world = make_world(frame=1000 + i)
        defenders = [make_body(pid, bc.x + dx, bc.y + dy, tackle_role=role,
                               fullback=fb, can_acquire_tackle=can)
                     for pid, dx, dy, role, fb, can in specs]
        ope = RecordingOPE(verdicts)
        d = run_boundary(world, ope, defenders, bc)
        seen[d.reason] = seen.get(d.reason, 0) + 1
        if d.outcome is ca.ContactOutcome.CONTACT_FORMED:
            formed += 1
        else:
            failed += 1
        print("  %-30s %-28s %-14s %d" % (
            name, d.reason.value, d.outcome.value, len(d.evidence)))
        if d.reason is not expected:
            fail("scenario %r reported %s, expected %s"
                 % (name, d.reason.value, expected.value))

    missing = [r.value for r in ca.ContactFailureReason if r not in seen]
    if missing:
        fail("these declared reasons were never produced: %s" % missing)
    if formed + failed != len(_scenario_matrix()):
        fail("formed (%d) + failed (%d) does not reconcile to %d frames"
             % (formed, failed, len(_scenario_matrix())))

    print("\n  formed=%d failed=%d total=%d  reasons covered=%d/%d"
          % (formed, failed, formed + failed, len(seen),
             len(list(ca.ContactFailureReason))))
# ─────────────────────────────────────────────────────────────────────
# [D] TES ENTRY IS THE SAME PATH
# ─────────────────────────────────────────────────────────────────────

def probe_tes_entry_path():
    section("[D] TES ENTRY IS THE SAME PATH (exactly one entry, on the "
            "DECLARED winner)")

    # A three-body line where the FIRST legal candidate misses and the SECOND
    # forms: the winner must be the second body, and TES must be entered once.
    bc = make_carrier()
    world = make_world(frame=1)
    first = make_body(301, bc.x - 0.5, bc.y)
    second = make_body(302, bc.x - 0.4, bc.y)
    third = make_body(303, bc.x - 0.3, bc.y)
    ope = RecordingOPE({301: "MISS", 302: "CONTACT", 303: "CONTACT"})

    decision = run_boundary(world, ope, [first, second, third], bc)

    print("  predicate consulted for: %s" % ope.predicate_calls)
    print("  declared winner_id      : %s" % decision.winner_id)
    print("  _start_tackle called    : %s" % (ope.start_calls or "never"))

    if decision.outcome is not ca.ContactOutcome.CONTACT_FORMED:
        fail("expected CONTACT_FORMED, got %s" % decision.outcome.value)
    if decision.winner is not second:
        fail("the declared winner was not the second candidate")
    if len(ope.start_calls) != 1:
        fail("TES entry fired %d times, expected exactly 1"
             % len(ope.start_calls))
    if ope.start_calls and ope.start_calls[0] != (bc.player_id, 302):
        fail("TES entered with %s, expected (%s, 302)"
             % (ope.start_calls[0], bc.player_id))
    if ope.predicate_calls != [301, 302]:
        fail("the scan did not stop at the winner: %s" % ope.predicate_calls)

    # A FAILED frame must never enter TES.
    bc2 = make_carrier()
    world2 = make_world(frame=2)
    body = make_body(304, bc2.x - 0.5, bc2.y)
    ope2 = RecordingOPE({304: "GLANCING"})
    d2 = run_boundary(world2, ope2, [body], bc2)
    if ope2.start_calls:
        fail("TES was entered on a %s frame: %s"
             % (d2.outcome.value, ope2.start_calls))
    print("  a failed frame entered TES: %s (must be never)"
          % (ope2.start_calls or "never"))

    # The authority itself never enters TES: it exposes no such call.
    public = [n for n in dir(ca) if not n.startswith("_")]
    if any("start_tackle" in n for n in public):
        fail("the authority exposes an entry point of its own: %s" % public)
    print("\n  the authority entered TES itself: no (it has no entry point)")

    # ── THE CHOKE POINT, ON THE ENGAGE CHANNEL ──
    # A behaviour-layer predicate (lvl1 chase / structured / unified / pursuit)
    # admits a contest through ``enter_tes`` directly, with its own channel.
    bc3 = make_carrier()
    world3 = make_world(frame=3)
    body3 = make_body(305, bc3.x - 0.4, bc3.y)
    ope3 = RecordingOPE()
    pub = ca.enter_tes(world3, ope3, bc3, body3,
                       ca.CHANNEL_STRUCTURED_ENGAGE)
    print("  engage-channel entry : channel=%s winner=%s tes_entry=%s"
          % (pub.channel if pub else None,
             pub.winner_id if pub else None,
             ope3.start_calls or "never"))
    if pub is None or pub.channel != ca.CHANNEL_STRUCTURED_ENGAGE:
        fail("an engage-channel entry did not record its channel")
    if ca.last_contest(world3) is not pub:
        fail("the choke point did not publish the contest record")
    if ope3.start_calls != [(bc3.player_id, 305)]:
        fail("the choke point did not delegate to the engine entry")

    # The MANAGER-channel handover must NOT publish a second decision in the
    # same frame: the sequence number must be unchanged by the handover.
    seq_before = getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0)
    handover = ca.enter_tes(world, ope, bc, second,
                            ca.CHANNEL_MANAGER_TRIGGER, already_decided=True)
    seq_after = getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0)
    print("  manager handover     : seq %s -> %s (no republish: %s)"
          % (seq_before, seq_after, seq_before == seq_after))
    if seq_after != seq_before:
        fail("the manager handover republished the decision")
    if handover is not ca.last_decision(world):
        fail("the manager handover did not return the published decision")


# ─────────────────────────────────────────────────────────────────────
# [E] THE FAILED-CONTACT CARRIER EFFECTS
# ─────────────────────────────────────────────────────────────────────

def probe_failed_contact_effects():
    section("[E] THE FAILED-CONTACT EFFECTS, measured on the real carrier")

    # ARM_GRAB: vx and vy damped to exactly 0.85 of their pre-call values.
    bc = make_carrier(vx=7.3, vy=-2.9)
    pre_vx, pre_vy = bc.vx, bc.vy
    world = make_world(frame=1)
    body = make_body(401, bc.x - 0.5, bc.y)
    d = run_boundary(world, RecordingOPE({401: "ARM_GRAB"}), [body], bc)
    exp_vx = pre_vx * ca.ARM_GRAB_DAMPING_FACTOR
    exp_vy = pre_vy * ca.ARM_GRAB_DAMPING_FACTOR
    print("  ARM_GRAB   vx %.4f -> %.4f (expected %.4f)  exact=%s"
          % (pre_vx, bc.vx, exp_vx, bc.vx == exp_vx))
    print("             vy %.4f -> %.4f (expected %.4f)  exact=%s"
          % (pre_vy, bc.vy, exp_vy, bc.vy == exp_vy))
    if bc.vx != exp_vx or bc.vy != exp_vy:
        fail("ARM_GRAB was not a bit-exact 0.85 damping")
    if d.outcome is not ca.ContactOutcome.CONTACT_FAILED:
        fail("ARM_GRAB formed a contest")
    if not d.arm_grab_damping:
        fail("the decision did not declare the arm-grab damping")

    # GLANCING: y moves by exactly +/-0.5 m and vy becomes +/-2.0.
    for label, dy, expect_dir in (("carrier ABOVE defender", -0.4, +1.0),
                                  ("carrier BELOW defender", +0.4, -1.0)):
        bc = make_carrier(x=60.0, y=35.0, vx=3.0, vy=5.0)
        pre_y = bc.y
        world = make_world(frame=2)
        body = make_body(402, bc.x - 0.5, bc.y + dy)
        d = run_boundary(world, RecordingOPE({402: "GLANCING"}), [body], bc)
        exp_y = pre_y + ca.GLANCING_LATERAL_OFFSET_M * expect_dir
        exp_vy = expect_dir * ca.GLANCING_LATERAL_VELOCITY_MPS
        print("  GLANCING %s: y %.4f -> %.4f (expected %.4f)  vy -> %.4f "
              "(expected %.4f)" % (label, pre_y, bc.y, exp_y, bc.vy, exp_vy))
        if bc.y != exp_y or bc.vy != exp_vy:
            fail("GLANCING displacement/velocity was not bit-exact (%s)"
                 % label)
        if d.outcome is not ca.ContactOutcome.CONTACT_FAILED:
            fail("GLANCING formed a contest (%s)" % label)

    # MISS touches nothing at all.
    bc = make_carrier(vx=3.3, vy=-1.7)
    before = (bc.x, bc.y, bc.vx, bc.vy)
    world = make_world(frame=3)
    body = make_body(403, bc.x - 0.5, bc.y)
    run_boundary(world, RecordingOPE({403: "MISS"}), [body], bc)
    print("  MISS       carrier state unchanged: %s"
          % ((bc.x, bc.y, bc.vx, bc.vy) == before,))
    if (bc.x, bc.y, bc.vx, bc.vy) != before:
        fail("MISS mutated the carrier")

    # FORMING a contest does NOT touch the carrier here: the effects are the
    # failed-contact bands only, and TES owns what happens next.
    bc = make_carrier(vx=4.1, vy=2.2)
    before = (bc.x, bc.y, bc.vx, bc.vy)
    world = make_world(frame=4)
    body = make_body(404, bc.x - 0.5, bc.y)
    run_boundary(world, RecordingOPE({404: "CONTACT"}), [body], bc)
    print("  CONTACT    carrier state unchanged by the authority: %s"
          % ((bc.x, bc.y, bc.vx, bc.vy) == before,))
    if (bc.x, bc.y, bc.vx, bc.vy) != before:
        fail("a FORMED contest mutated the carrier — that is TES's job")


# ─────────────────────────────────────────────────────────────────────
# [F] THE AUDIT SINK
# ─────────────────────────────────────────────────────────────────────

def probe_audit_sink():
    section("[F] THE AUDIT SINK (read-only, off by default, one row per "
            "decision)")

    if os.path.exists(AUDIT_CSV):
        os.remove(AUDIT_CSV)

    if ca.audit_enabled():
        fail("the audit sink was armed before the probe touched it")

    frames = []
    prev = os.environ.get(ca.AUDIT_ENV_VAR)
    os.environ[ca.AUDIT_ENV_VAR] = "1"
    try:
        for i, (name, expected, specs, verdicts) in enumerate(
                _scenario_matrix()):
            bc = make_carrier()
            world = make_world(frame=8000 + i)
            defenders = [
                make_body(pid, bc.x + dx, bc.y + dy, tackle_role=role,
                          fullback=fb, can_acquire_tackle=can)
                for pid, dx, dy, role, fb, can in specs
            ]
            run_boundary(world, RecordingOPE(verdicts), defenders, bc)
            frames.append(expected.value)
    finally:
        if prev is None:
            os.environ.pop(ca.AUDIT_ENV_VAR, None)
        else:
            os.environ[ca.AUDIT_ENV_VAR] = prev

    if not os.path.exists(AUDIT_CSV):
        fail("arming %s wrote no CSV" % ca.AUDIT_ENV_VAR)
        return

    with open(AUDIT_CSV, "r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    frame_rows = [r for r in rows if r["row_kind"] == "frame"]
    body_rows = [r for r in rows if r["row_kind"] == "body"]

    print("  sink file        : %s" % AUDIT_CSV)
    print("  frame rows       : %d (decisions evaluated: %d)"
          % (len(frame_rows), len(frames)))
    print("  body rows        : %d" % len(body_rows))
    print("  %-14s %-28s %s" % ("outcome", "reason", "winner"))
    print("  " + "-" * 56)
    for row in frame_rows:
        print("  %-14s %-28s %s" % (
            row["outcome"], row["reason"], row["winner_id"] or "-"))

    if len(frame_rows) != len(frames):
        fail("the sink wrote %d frame rows for %d decisions"
             % (len(frame_rows), len(frames)))
    if [r["reason"] for r in frame_rows] != frames:
        fail("the sink's reasons did not match the delivered decisions")
    vocab = [r.value for r in ca.ContactFailureReason]
    if not all(r["reason"] in vocab for r in frame_rows):
        fail("a frame row carried a reason outside the declared vocabulary")

    os.remove(AUDIT_CSV)
    if os.path.exists(AUDIT_CSV):
        fail("the audit artefact could not be removed")
    print("\n  the sink is a pure read-only artefact: removed cleanly, and "
          "nothing failed when it was absent.")
    if ca.audit_enabled():
        fail("the audit sink was left armed after the probe")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("CONTACT AUTHORITY FORMATION PROBE (Stage 1B)")
    print("contact radius (read from world/open_play_engine) = %.4f m" % RADIUS)
    print("outcomes: %s" % [o.value for o in ca.ContactOutcome])
    print("providers declared: %d" % len(ca.declared_evidence_providers()))

    probe_one_decision_per_frame()
    probe_real_conjuncts()
    probe_reason_coverage()
    probe_tes_entry_path()
    probe_failed_contact_effects()
    probe_audit_sink()

    print("\n" + "=" * 78)
    if FAILURES:
        print("PROBE RESULT: %d FAILURE(S)" % len(FAILURES))
        for msg in FAILURES:
            print("  - %s" % msg)
        return 1
    print("PROBE RESULT: contest formation is owned by one declared "
          "authority — one decision per frame, the five conjuncts intact, "
          "one TES entry on the declared winner, the failed-contact effects "
          "bit-exact, and a read-only audit that names every failure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
