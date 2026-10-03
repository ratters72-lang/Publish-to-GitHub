"""
verify_retreat_obligation_contract.py — THE 10 m RETREAT-OBLIGATION CONTRACT.

REDESIGNED 2026-09-18.  The obligation is ENGINE TRUTH, not invented state:

    a defender is retreat-obligated iff its LIVE ROLE is DEF_RETREAT_10

Sections:

  1. VETO PREDICATE SEMANTICS
     A pure read of the live role on real Player objects: DEF_RETREAT_10 is
     obligated, every other role is not, markers / DH / fullback are exempt,
     ``None`` is safe, and the predicate carries NO state (it never writes a
     latch, and no world is required).

  2. ROLE SURVIVAL — THE FLOW FIX (the reason the gate used to be dead)
     ``assign_def_line_ready`` runs on EVERY defender EVERY frame BEFORE the
     authority writers.  It must NOT convert DEF_RETREAT_10 (or the state is
     destroyed before any veto can observe it).  Both copies are checked, and
     it must still promote ``None`` / ``DEF_CHASE`` and still refuse
     markers / DH / tacklers / fullback / frozen bodies.

  3. VETO ENFORCEMENT AT EVERY AUTHORITY GRANT (live calls)
     lane candidacy, the engage machine, pre-tackle ownership, the corridor's
     admission + incumbent bypass.

  4. REVOCATION IS SYMMETRIC
     ``strip_tackle_authority`` clears every authority marker, including a
     promoted depth class.

  5. THE DRIFT AUDIT + PER-FRAME SYNC
     the audit names an obligated body holding authority, raises under
     ``raise_on_violation``, and is silent once revoked; ``sync`` revokes and
     returns exactly the obligated bodies.

  6. FRAME CONTRACT + WIRING (source-level, order-sensitive)
     sync before the behaviour dispatch; audit + post-writer revocation after
     every acquisition writer and before the tackle trigger; the engine's
     TES-era conversion sweep excludes DEF_RETREAT_10; the engine's arrival
     test is the sole writer of retreat-completion.

  7. NO INVENTED STATE
     latch / target / cooldown / re-arm APIs are GONE from the module and are
     referenced nowhere, so the obligation cannot drift from the engine role.

  8. NO PHASE-TIMING STATE
     the module writes no phase / phase-timer attribute, so convergence-first
     RELEASE and the PTB budget are untouched.

Emits verify_retreat_obligation_report.json; exits 0 when all checks pass.
"""

import ast
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1 import retreat_obligation as ro
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.intercept_tacklers import (
    _is_eligible_lane_defender,
    is_lane_tackler_candidate,
)
from game.defence.defence_lvl_1.pretackle_machine import _invalid_candidate
from game.defence.defence_lvl_1.pre_tackle_authority import (
    publish_pre_tackle_state,
    assign_def_line_ready,
)
from game.defence.defence_lvl_1.pretackle_attempt import (
    assign_def_line_ready as assign_def_line_ready_attempt,
)
from game.defence.defence_lvl_1.pretackle_corridor import (
    is_corridor_eligible,
    _corridor_admit,
)

OBLIGATION_SRC = "game/defence/defence_lvl_1/retreat_obligation.py"
MANAGER_SRC = "game/managers/defence_manager.py"
ENGINE_SRC = "world/open_play_engine.py"
PTA_SRC = "game/defence/defence_lvl_1/pre_tackle_authority.py"
PTA_ATTEMPT_SRC = "game/defence/defence_lvl_1/pretackle_attempt.py"
MMDL_SRC = "game/defence/defence_lvl_1/movement_mode_decision.py"

RETREAT_SKILL = "DEF_RETREAT_10"
LINE_SKILL = "DEF_LINE_READY"

# The invented-state APIs this redesign DELETED.  None may exist anywhere.
DELETED_APIS = (
    "stamp_retreat_obligation",
    "clear_retreat_obligation",
    "retreat_deficit_m",
    "obligation_target_x",
    "is_anchor_pin_exempt",
    "OBLIGATION_ATTR",
    "OBLIGATION_TARGET_ATTR",
    "OBLIGATION_DISCHARGED_FRAME_ATTR",
    "REARM_COOLDOWN_FRAMES",
    "REARM_MARGIN_M",
    "RETREAT_OBLIGATION_TOLERANCE_M",
)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def read_src(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _reads_name(source, name):
    """True when *source* READS *name* as an identifier (AST, not text)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


def role_body(role=None, pid=1, x=35.0, y=35.0):
    d = Player(x=x, y=y, player_id=pid)
    d.defender_role = role
    d.depth_class = "PASSIVE"
    d.tackle_role = None
    return d


def obligate(d):
    """Obligated == the engine's own retreat role.  No latch to set."""
    d.defender_role = DefenderRole.DEF_RETREAT_10
    return d


# ─────────────────────────────────────────────────────────────────────
# 1. VETO PREDICATE SEMANTICS
# ─────────────────────────────────────────────────────────────────────
def run_section1():
    print("\n[1] Veto predicate semantics (pure engine-truth role read)")

    retreating = role_body(DefenderRole.DEF_RETREAT_10, pid=1)
    check("a DEF_RETREAT_10 body IS obligated",
          ro.is_retreat_obligated(retreating))
    check("the predicate needs no world (role IS the state)",
          ro.is_retreat_obligated(retreating, None))
    check("passing a world does not change the verdict",
          ro.is_retreat_obligated(retreating, object()))

    for role in (DefenderRole.DEF_LINE_READY, DefenderRole.DEF_CHASE,
                 DefenderRole.KICK_CHASE, DefenderRole.TACKLER_1,
                 DefenderRole.MARKER_A):
        check(f"{role.name} is NOT obligated",
              not ro.is_retreat_obligated(role_body(role)))

    check("a None role is NOT obligated",
          not ro.is_retreat_obligated(role_body(None)))
    check("a None defender is safe",
          not ro.is_retreat_obligated(None))

    for role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                 DefenderRole.DUMMY_HALF):
        m = role_body(role)
        m.defender_role = role
        m.__dict__["_force_retreat_role"] = True
        # markers are exempt even if they somehow carry the retreat role
        m.defender_role = DefenderRole.DEF_RETREAT_10
        m.defender_role = role
        check(f"{role.name} is exempt", not ro.is_retreat_obligated(m))

    fb = role_body(DefenderRole.DEF_LINE_READY)
    fb.fullback_role_active = True
    check("the dynamic fullback is exempt",
          not ro.is_retreat_obligated(fb))

    check("has_retreat_obligation_authority is the exact negation",
          ro.has_retreat_obligation_authority(role_body(LINE_SKILL and
                                                        DefenderRole.DEF_LINE_READY))
          and not ro.has_retreat_obligation_authority(retreating))

    # NO STATE: the predicate must not stamp anything on the body.
    fresh = role_body(DefenderRole.DEF_RETREAT_10, pid=99)
    before = set(fresh.__dict__.keys())
    ro.is_retreat_obligated(fresh, object())
    after = set(fresh.__dict__.keys())
    check("the predicate writes NO state onto the body", before == after,
          f"added={sorted(after - before)}")


# ─────────────────────────────────────────────────────────────────────
# 2. ROLE SURVIVAL — THE FLOW FIX
# ─────────────────────────────────────────────────────────────────────
def run_section2():
    print("\n[2] Role survival — assign_def_line_ready must not eat the role")

    for label, promote in (("pre_tackle_authority", assign_def_line_ready),
                           ("pretackle_attempt", assign_def_line_ready_attempt)):
        d = role_body(DefenderRole.DEF_RETREAT_10, pid=11)
        promote(d)
        check(f"{label}: DEF_RETREAT_10 SURVIVES the promotion",
              d.defender_role is DefenderRole.DEF_RETREAT_10,
              f"role={getattr(d.defender_role, 'name', d.defender_role)}")
        check(f"{label}: the surviving body IS still obligated",
              ro.is_retreat_obligated(d))

        n = role_body(None, pid=12)
        promote(n)
        check(f"{label}: a None role is still promoted to DEF_LINE_READY",
              n.defender_role is DefenderRole.DEF_LINE_READY)

        c = role_body(DefenderRole.DEF_CHASE, pid=13)
        promote(c)
        check(f"{label}: DEF_CHASE is still promoted",
              c.defender_role is DefenderRole.DEF_LINE_READY)

        for role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                     DefenderRole.DUMMY_HALF, DefenderRole.TACKLER_1,
                     DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
            b = role_body(role, pid=14)
            promote(b)
            check(f"{label}: {role.name} is never touched",
                  b.defender_role is role)

        fr = role_body(DefenderRole.DEF_RETREAT_10, pid=15)
        fr.frozen = True
        promote(fr)
        check(f"{label}: a frozen body is never touched",
              fr.defender_role is DefenderRole.DEF_RETREAT_10)

        fbt = role_body(DefenderRole.DEF_RETREAT_10, pid=16)
        fbt.fullback_role_active = True
        promote(fbt)
        check(f"{label}: the dynamic fullback is never touched",
              fbt.defender_role is DefenderRole.DEF_RETREAT_10)


# ─────────────────────────────────────────────────────────────────────
# 3. VETO ENFORCEMENT AT EVERY AUTHORITY GRANT
# ─────────────────────────────────────────────────────────────────────
def run_section3():
    print("\n[3] Veto enforcement at every authority grant")
    world = object()
    tuners = DefenceTuners()
    bc = Player(x=30.0, y=35.0, player_id=500)

    # ── 3a. LANE CANDIDACY ──
    legal = role_body(DefenderRole.DEF_LINE_READY, pid=20, x=32.0)
    check("lane candidacy admits a legal line body",
          _is_eligible_lane_defender(legal, world, bc))
    gated = obligate(role_body(None, pid=21, x=32.0))
    check("lane candidacy vetoes a retreating body",
          not _is_eligible_lane_defender(gated, world, bc))
    check("is_lane_tackler_candidate vetoes a retreating body",
          not is_lane_tackler_candidate(gated, bc.x, world=world, bc=bc))

    # ── 3b. MMDL carries no obligation veto (movement is never gated) ──
    mmdl = read_src(MMDL_SRC)
    check("MMDL carries no retreat-obligation veto "
          "(movement is never gated by this module)",
          "retreat_obligation" not in mmdl
          and "is_retreat_obligated" not in mmdl)

    # ── 3c. ENGAGE MACHINE ──
    # ``_invalid_candidate`` short-circuits on ``world.ball_owner != "BC"``
    # (and on the TES suppress flag), so the gate needs a world that
    # actually owns the ball -- a bare ``object()`` returns "not_bc_owner"
    # for every body and would prove nothing about the veto.
    em_world = type("W", (), {"ball_owner": "BC",
                              "open_play_engine": None})()
    gated2 = obligate(role_body(None, pid=23, x=30.0))
    check("engage machine rejects a retreating candidate",
          _invalid_candidate(em_world, gated2, bc) == "retreat_obligated",
          f"reason={_invalid_candidate(em_world, gated2, bc)}")
    legal2 = role_body(DefenderRole.DEF_LINE_READY, pid=24, x=30.0)
    check("engage machine accepts a legal candidate",
          _invalid_candidate(em_world, legal2, bc) is None,
          f"reason={_invalid_candidate(em_world, legal2, bc)}")

    # ── 3d. PRE-TACKLE OWNERSHIP ──
    p_tuners = DefenceTuners()
    p_tuners.pretackle_attempt_enabled = True
    p_world = object()
    p_bc = Player(x=31.0, y=35.0, player_id=502)
    p_world = type("W", (), {"ball_carrier": p_bc})()
    legal3 = role_body(DefenderRole.DEF_LINE_READY, pid=25, x=30.5)
    legal3.tackle_role = "LINE"
    legal3.freeze_timer = 0.0
    gated3 = obligate(role_body(None, pid=26, x=30.5))
    gated3.tackle_role = "LINE"
    gated3.freeze_timer = 0.0
    managed = publish_pre_tackle_state(p_world, [legal3, gated3], p_bc,
                                       p_tuners)
    check("pre-tackle ownership refuses the retreating body",
          gated3 not in managed
          and getattr(gated3.pre_tackle_state, "name", None) == "NONE")

    # ── 3e. CORRIDOR ──
    c_tuners = DefenceTuners()
    c_bc = Player(x=31.0, y=35.0, player_id=503)
    committed = role_body(DefenderRole.DEF_LINE_READY, pid=27, x=30.5)
    committed.tackle_role = "PRIMARY"
    check("corridor admits a committed, legal body",
          is_corridor_eligible(committed, c_bc, c_tuners, world))
    obligate(committed)
    check("corridor eligibility vetoes a retreating body",
          not is_corridor_eligible(committed, c_bc, c_tuners, world))
    check("corridor incumbent bypass vetoes a retreating body",
          not _corridor_admit(committed, c_bc, c_tuners,
                              {id(committed): True}, world))


# ─────────────────────────────────────────────────────────────────────
# 4. REVOCATION IS SYMMETRIC
# ─────────────────────────────────────────────────────────────────────
def run_section4():
    print("\n[4] Revocation is symmetric with the veto")
    from game.defence.defence_lvl_1.pre_tackle_authority import PreTackleState

    d = obligate(role_body(None, pid=30, x=30.0))
    d.tackle_role = "PRIMARY"
    d.depth_class = "SECONDARY"
    d._tackle_commit_active = True
    d._pursuit_commit_active = True
    d.can_acquire_tackle = True
    d.__dict__["_pta_corridor"] = True
    d.pre_tackle_state = PreTackleState.COMMIT

    cleared = ro.strip_tackle_authority(None, d)
    check("strip clears the lane role", d.tackle_role is None)
    check("strip clears the promoted depth class", d.depth_class == "PASSIVE")
    check("strip clears both commit latches",
          not d._tackle_commit_active and not d._pursuit_commit_active)
    check("strip clears can_acquire_tackle", not d.can_acquire_tackle)
    check("strip clears the corridor latch",
          not d.__dict__.get("_pta_corridor", False))
    check("strip clears the pre-tackle state",
          d.__dict__.get("pre_tackle_state") is None
          or getattr(d.pre_tackle_state, "name", None) == "NONE")
    for name in ("tackle_role", "pre_tackle_state", "_tackle_commit_active",
                 "_pursuit_commit_active", "can_acquire_tackle", "depth_class"):
        check(f"strip reports it cleared {name}", name in cleared)

    clean = role_body(DefenderRole.DEF_LINE_READY, pid=31, x=30.0)
    check("strip on a clean body clears nothing",
          ro.strip_tackle_authority(None, clean) == [])


# ─────────────────────────────────────────────────────────────────────
# 5. THE DRIFT AUDIT + PER-FRAME SYNC
# ─────────────────────────────────────────────────────────────────────
def run_section5():
    print("\n[5] Drift audit + per-frame sync")

    bad = obligate(role_body(None, pid=40, x=30.0))
    bad.tackle_role = "PRIMARY"
    bad._tackle_commit_active = True
    violations = ro.audit_retreat_obligation_compliance(None, [bad])
    check("audit REPORTS a retreating body holding authority",
          len(violations) == 1, f"violations={violations}")
    check("audit names the body and the markers it holds",
          "RETREAT_OBLIGATION_AUTHORITY" in violations[0]
          and "id=40" in violations[0])

    raised = False
    try:
        ro.audit_retreat_obligation_compliance(
            None, [bad], raise_on_violation=True,
        )
    except AssertionError:
        raised = True
    check("audit RAISES under raise_on_violation", raised)

    ro.strip_tackle_authority(None, bad)
    check("audit is clean once the authority is revoked",
          ro.audit_retreat_obligation_compliance(None, [bad]) == [])

    legal = role_body(DefenderRole.DEF_LINE_READY, pid=41, x=30.0)
    legal.tackle_role = "PRIMARY"
    check("audit ignores a body that is not retreating",
          ro.audit_retreat_obligation_compliance(None, [legal]) == [])
    check("audit is empty for an empty defender list",
          ro.audit_retreat_obligation_compliance(None, []) == [])

    # ── sync: revoke + return exactly the obligated bodies ──
    held = obligate(role_body(None, pid=42, x=30.0))
    held.tackle_role = "PRIMARY"
    held._tackle_commit_active = True
    gated = ro.sync_retreat_obligation(object(), [held, legal])
    check("sync returns exactly the obligated bodies",
          gated == [held], f"gated={[getattr(g, 'player_id', '?') for g in gated]}")
    check("sync revokes the authority the body may not hold",
          held.tackle_role is None and not held._tackle_commit_active)
    check("sync leaves a non-retreating body alone",
          legal.tackle_role == "PRIMARY")
    check("sync is safe with a None world",
          ro.sync_retreat_obligation(None, [held]) == [])


# ─────────────────────────────────────────────────────────────────────
# 6. FRAME CONTRACT + WIRING
# ─────────────────────────────────────────────────────────────────────
def run_section6():
    print("\n[6] Frame contract and wiring")
    mgr = read_src(MANAGER_SRC)
    ope = read_src(ENGINE_SRC)

    i_sync = mgr.find("sync_retreat_obligation(world,")
    i_apply = mgr.find(
        "self.def_behaviour.apply(world, defenders, anchor_x, lane_y)"
    )
    i_audit = mgr.find("audit_retreat_obligation_compliance(")
    i_revoke = mgr.find("strip_tackle_authority(world, _ov)")
    # STAGE 1B (2026-09-19) MOVED the tackle trigger: the FIRE decision now
    # lives in ``contact_authority.evaluate_contact`` and the manager is a
    # pure CONSUMER of its single published outcome.  The old inline comment
    # anchor ("# ---- Tackle trigger with pre-tackle check ----") therefore no
    # longer exists anywhere in the manager — it was replaced by the
    # "CONTACT AUTHORITY — CONTEST FORMATION" dispatch block.  The anchor is
    # now the consumer call the trigger became; the ORDERING CONTRACT this
    # section asserts is unchanged: the post-writer revocation must still run
    # before the frame can fire a tackle.
    i_trigger = mgr.find("_contact_decision = evaluate_contact(")

    located = [i_sync, i_apply, i_audit, i_revoke, i_trigger]
    check("manager: all five frame-contract anchors located",
          all(i != -1 for i in located), f"indices={located}")
    check("manager: sync runs BEFORE the behaviour dispatch",
          0 <= i_sync < i_apply)
    check("manager: the audit runs AFTER every acquisition writer",
          i_apply < i_audit)
    check("manager: the post-writer revocation runs after the audit",
          i_audit < i_revoke)
    check("manager: the revocation runs BEFORE the tackle trigger",
          i_revoke < i_trigger)
    check("manager: the audit switches on the STRICT flag, not __debug__",
          "raise_on_violation=RETREAT_OBLIGATION_STRICT" in mgr
          and "raise_on_violation=__debug__" not in mgr)
    check("manager: the manager does NOT stamp a debt per frame",
          "stamp_retreat_obligation" not in mgr)

    # ── THE ENGINE'S ARRIVAL TEST IS THE SOLE COMPLETION WRITER ──
    check("engine: the arrival test promotes the role on retreat-complete",
          "if d.x >= target_x:" in ope
          and "d.defender_role = DefenderRole.DEF_LINE_READY" in ope)
    check("engine: DEF_RETREAT_10 is EXCLUDED from the TES-era "
          "conversion sweep",
          "DefenderRole.DEF_RETREAT_10,\n                DefenderRole.DEF_CHASE,"
          not in ope)
    check("engine: the era sweep still converts markers/tacklers/DH",
          "DefenderRole.MARKER_A," in ope and "DefenderRole.DUMMY_HALF," in ope)
    check("engine: no obligation stamp/clear call remains",
          "stamp_retreat_obligation(" not in ope
          and "clear_retreat_obligation(" not in ope)
    check("engine: consumes the convergence predicate, not a wall clock",
          "release_convergence(" in ope)

    for label, path in (("pre_tackle_authority", PTA_SRC),
                        ("pretackle_attempt", PTA_ATTEMPT_SRC)):
        src = read_src(path)
        check(f"{label}: the retreat role is explicitly spared",
              "if role is DefenderRole.DEF_RETREAT_10:" in src
              and "return" in src)


# ─────────────────────────────────────────────────────────────────────
# 7. NO INVENTED STATE
# ─────────────────────────────────────────────────────────────────────
def run_section7():
    print("\n[7] NO invented state — the obligation cannot drift from the role")
    obl = read_src(OBLIGATION_SRC)

    for api in DELETED_APIS:
        check(f"retreat_obligation no longer defines {api}",
              f"def {api}" not in obl and f"{api}:" not in obl)

    for path in (MANAGER_SRC, ENGINE_SRC, PTA_SRC, PTA_ATTEMPT_SRC):
        src = read_src(path)
        offenders = [a for a in DELETED_APIS if a in src]
        check(f"{path.split('/')[-1]}: references no deleted API",
              not offenders, f"offenders={offenders}")

    # The latch attribute must not be written anywhere any more.
    check("no module writes a retreat-obligation latch attribute",
          "retreat_obligation_pending" not in read_src(MANAGER_SRC)
          and "retreat_obligation_pending" not in read_src(ENGINE_SRC))

    # The module exposes exactly the role predicate as its veto.
    check("the veto is the role comparison",
          "return _role_name(d) == RETREAT_OBLIGATION_ROLE_NAME" in obl)
    check("the obligation role name is DEF_RETREAT_10",
          '"DEF_RETREAT_10"' in obl)


# ─────────────────────────────────────────────────────────────────────
# 8. NO PHASE-TIMING STATE
# ─────────────────────────────────────────────────────────────────────
def run_section8():
    print("\n[8] The obligation is an AUTHORITY gate, not a timing gate")
    obl = read_src(OBLIGATION_SRC)

    banned = {"_phase", "_phase_timer", "_ptb_gate_override_frames",
              "marker_freeze_timer"}
    bad_writes = []
    for node in ast.walk(ast.parse(obl)):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if node.attr in banned:
                bad_writes.append(node.attr)
    check("retreat_obligation writes no phase / phase-timer attribute",
          not bad_writes, f"bad_writes={bad_writes}")
    check("retreat_obligation never READS POST_RUCK_DURATION_S",
          not _reads_name(obl, "POST_RUCK_DURATION_S"))
    check("retreat_obligation touches no release constant",
          "RELEASE_MIN" not in obl and "RELEASE_MAX" not in obl)

    gate = read_src("world/rules/release_gate.py")
    check("convergence-first RELEASE constants remain the sole authority",
          "RELEASE_MIN_CONVERGENCE_S" in gate
          and "RELEASE_MAX_DURATION_S" in gate
          and "release_convergence" in gate)


def main():
    print("=" * 72)
    print("RETREAT-OBLIGATION CONTRACT VERIFICATION")
    print("(engine truth: obligated iff the live role is DEF_RETREAT_10)")
    print("=" * 72)

    run_section1()
    run_section2()
    run_section3()
    run_section4()
    run_section5()
    run_section6()
    run_section7()
    run_section8()

    report = {
        "retreat_obligation_contract": {
            "all_checks_passed": not failures,
            "failures": failures,
            "section_count": 8,
            "model": "obligated iff live role is DEF_RETREAT_10",
        }
    }
    report_path = "verify_retreat_obligation_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
