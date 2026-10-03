"""verify_contact_authority_contract.py — THE CONTACT AUTHORITY CONTRACT.

Stage 1B.  Machine-checks every decision of the contact-authority declaration
(``game.defence.defence_lvl_1.contact_authority``):

  [1] THE SURFACE — one declared authority exists; exactly two outcomes; one
      decision per frame; the decision carries the declared formation data;
      and ``enter_tes`` is the single declared TES-entry choke point.
  [2] THE CONJUNCTS — the five conjuncts of the live trigger, in order, with
      the first-wins short-circuit.
  [3] DIFFERENTIAL EQUIVALENCE — the authority vs an INDEPENDENTLY WRITTEN copy
      of the loop it replaced, over a deterministic input matrix: identical
      outcome, identical winner, BIT-IDENTICAL carrier mutations.  This is the
      machine-checked "no gameplay behaviour changes" claim.
  [4] EVIDENCE PROVIDERS — the writers are consumed, not re-derived, and are
      unmodified in the respects formation depends on.  The authority writes
      nothing but the carrier's declared failed-contact effects.
  [5] TES ENTRY PATH — EXACTLY ONE reachable choke point.  ``_start_tackle``
      stays the engine's single entry, and the authority's ``enter_tes`` is the
      only REACHABLE caller; every contest-admitting module routes through it
      with its channel.  Call sites inside unreferenced ``_disabled_*`` copies
      are not reachable code and are exempted EXPLICITLY, with the exemption
      itself asserted (a referenced ``_disabled_`` body fails the scan).
  [6] NO BEHAVIOUR CHANGE — the predicate, its thresholds and the contact
      radius are untouched and read from their ORIGINAL owners; there is no
      behavioural canary (and the verifier asserts the absence, with reason).
  [7] AUDIT — every evaluated body carries a reason from the declared
      vocabulary; formed + failed reconciles; the sink is read-only and off by
      default.
  [8] IMPORT SAFETY + NO INVENTED STATE — stdlib-only module scope, no cycle,
      no latch / cooldown / target store.

The verifier is PURE and HEADLESS: attribute stubs and constructed bodies, so
it never boots the engine and never depends on run-to-run non-determinism.
(Importing ``world.open_play_engine`` at module scope, for the radius and the
differential harness, is read-only: it prints its own banner and nothing else.)

Exit code: 0 = all checks passed, 1 = at least one check failed.

Run:  python verify_contact_authority_contract.py
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import ast
import csv
import json
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import DefenderRole
from game.defence.defence_lvl_1 import contact_authority as ca
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

MODULE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "contact_authority.py",
)
MANAGER_PATH = os.path.join("game", "managers", "defence_manager.py")
ENGINE_PATH = os.path.join("world", "open_play_engine.py")
TES_PATH = os.path.join("game", "tes", "tes.py")
TUNERS_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "defensive_tuners.py",
)
WRITER_PATHS = {
    "intercept_tacklers": os.path.join(
        "game", "defence", "defence_lvl_1", "intercept_tacklers.py",
    ),
    "pre_tackle_authority": os.path.join(
        "game", "defence", "defence_lvl_1", "pre_tackle_authority.py",
    ),
    "pretackle_corridor": os.path.join(
        "game", "defence", "defence_lvl_1", "pretackle_corridor.py",
    ),
    "pretackle_machine": os.path.join(
        "game", "defence", "defence_lvl_1", "pretackle_machine.py",
    ),
}
# The modules that can ADMIT a contest.  Each must reach TES only through
# ``contact_authority.enter_tes``.
ENGAGE_PATHS = {
    "defensive_behaviour_unified": os.path.join(
        "game", "defence", "defence_lvl_1",
        "defensive_behaviour_unified.py",
    ),
    "defensive_behaviour_lvl1": os.path.join(
        "game", "defence", "defence_lvl_1", "defensive_behaviour_lvl1.py",
    ),
}
# ── THE TES-ENTRY SCAN IS REPO-WIDE, NOT ALLOWLISTED ──────────────────
# An explicit directory allowlist is exactly how a sixth call site outside the
# listed directories hides.  The first Stage 1B shipment declared a "single
# entry" while four call sites in ALREADY-LISTED modules stayed live, so the
# failure mode is real.  The scan below walks the WHOLE repo and skips only
# named, historical/scratch locations -- and both skips are ASSERTED so they
# cannot silently grow.
SCAN_SKIP_DIRS = ("__pycache__", ".git", "debug_archive", "node_modules",
                  ".vscode", "venv", ".venv")

# The Stage 1B gates are not engine code: they EXERCISE the entry on purpose.
GATE_SCRIPTS = (
    "verify_contact_authority_contract.py",
    "probe_contact_authority_formation.py",
    "tmp_run_stage1b_regression.py",
    "tmp_run_stage1b_chain_battery.py",
    "tmp_s1b_neutralise_probe.py",
    "tmp_s1b_repo_scan.py",
)


def _is_engine_module(fname):
    """True only for a file whose name is an IMPORTABLE module name.

    ``defence_manager.taskxiv.bak.py`` is a backup.  Its module name would be
    ``...taskxiv.bak``, which cannot appear in an ``import`` statement, so the
    running engine can never load it.  Requiring ``str.isidentifier()`` on the
    stem is the declared, checkable rule that keeps backups out of the scan
    without hiding a single line of reachable code.
    """
    if not fname.endswith(".py"):
        return False
    return fname[:-3].isidentifier()
REPORT_PATH = "verify_contact_authority_contract_report.json"
AUDIT_CSV = ca.audit_sink_path()

TUNERS = DefenceTuners()
RADIUS = ca.contact_radius_m()

RESULTS = []


def check(sec, name, ok, detail=""):
    """Record and print one check result."""
    ok = bool(ok)
    RESULTS.append({"section": sec, "name": name, "ok": ok, "detail": detail})
    print("  [%s] %s%s" % (
        "PASS" if ok else "FAIL", name,
        (" - %s" % detail) if detail else "",
    ))
    return ok


def section(title):
    """Print a section banner."""
    print("\n%s" % title)


def read_src(path):
    """Read a source file as text (never raises on encoding)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _docstring_nodes(tree):
    """The Constant node of every module / class / function docstring."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def code_names(src):
    """Every identifier NAME referenced by EXECUTABLE code.

    Collects Name ids, Attribute attrs, keyword-argument names, and
    IDENTIFIER-SHAPED string literals -- the ``getattr(obj, "attr", None)``
    idiom keeps a name in a STRING, not in an Attribute node, so a literal is
    a code reference too.

    Docstrings are excluded, and so is any string that is not a bare
    identifier, so prose that merely NAMES a symbol (a docstring saying "TES
    entry stays in ``tes_start_tackle``") does not appear here.  This is how
    the verifier tells "the authority does not touch TES" apart from "the
    authority's documentation mentions TES".
    """
    tree = ast.parse(src)
    doc_ids = _docstring_nodes(tree)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif (isinstance(node, ast.Constant)
              and isinstance(node.value, str)
              and id(node) not in doc_ids
              and node.value.isidentifier()):
            names.add(node.value)
    return names


def call_sites(src, attr_name):
    """Line numbers of EXECUTABLE ``<expr>.<attr_name>(...)`` calls.

    AST-based, so a docstring or comment that merely NAMES the function is
    not a call site.  This is how the verifier proves that exactly ONE
    module in the engine can reach the TES entry.

    A file that will not parse as Python yields NO call sites instead of
    raising: the repo contains scratch ``.py`` files whose body is PROSE
    (one carries U+2011 hyphens and a trailing ``import sys, io, math``), and
    a repo-wide scan must survive them.  This tolerance cannot hide engine
    code -- ``verify_tes_entry_path`` separately asserts that EVERY file
    under an engine package parses.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr_name):
            out.append(node.lineno)
    return out


def attribute_stores(src):
    """Every ``obj.attr = ...`` target in the module: (base, attr, line).

    STORES only.  A local assignment named after a stamp --
    ``tackle_role = getattr(d, "tackle_role", None)`` -- is a READ of the
    stamp into a local and must never be mistaken for writing the stamp.
    """
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            base = node.value.id if isinstance(node.value, ast.Name) else None
            out.append((base, node.attr, node.lineno))
    return out


# ─────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────

class ScriptedOPE:
    """An OPE stub whose contact predicate returns SCRIPTED verdicts.

    The verdict for a body is looked up by ``player_id`` in ``verdicts``, so a
    test can construct any sequence of MISS / ARM_GRAB / GLANCING / CONTACT.
    ``calls`` records the order the predicate was consulted in, which is how
    the conjunct-order and short-circuit checks are made.
    """

    def __init__(self, verdicts=None, default="MISS"):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts or {})
        self.default = default
        self.calls = []
        # The TES entry the authority delegates to.  Recorded so the
        # ``enter_tes`` contract can be checked without an engine.
        self.start_calls = []

    def _check_pre_tackle(self, bc, defender):
        """Record the consult order and return the scripted verdict."""
        self.calls.append(getattr(defender, "player_id", None))
        return self.verdicts.get(
            getattr(defender, "player_id", None), self.default,
        )

    def _start_tackle(self, bc, defender):
        """The engine's single TES entry, recorded (never executed)."""
        self.start_calls.append((
            getattr(bc, "player_id", None),
            getattr(defender, "player_id", None),
        ))


def stub_world(frame=1, **attrs):
    """A minimal world object with the attributes the authority reads."""
    world = SimpleNamespace(
        frame_count=int(frame),
        ball_carrier=None,
        tes_active=False,
        ball_owner="BC",
        players=[],
        defending_team=[],
    )
    for key, value in attrs.items():
        setattr(world, key, value)
    return world


def stub_bc(pid=1, x=60.0, y=35.0, vx=4.0, vy=1.0):
    """A minimal ball-carrier object."""
    return SimpleNamespace(
        player_id=pid, x=float(x), y=float(y), vx=float(vx), vy=float(vy),
        facing=0.0, frozen=False, defender_role=None, tackle_role=None,
        depth_class=None, pre_tackle_state=None, fullback_role_active=False,
    )


def stub_body(pid, x, y, role="PRIMARY", **attrs):
    """A minimal candidate body with the declared evidence stamps."""
    d = SimpleNamespace(
        player_id=int(pid),
        x=float(x), y=float(y), vx=0.0, vy=0.0, facing=0.0,
        tackle_role=role,
        defender_role=DefenderRole.DEF_LINE_READY,
        depth_class="SECONDARY",
        pre_tackle_state=None,
        fullback_role_active=False,
        can_acquire_tackle=False,
        frozen=False,
        freeze_timer=0.0,
        tackle_state_active=False,
        _pta_corridor=False,
        _pta_corridor_frames=0,
    )
    for key, value in attrs.items():
        setattr(d, key, value)
    return d


def legacy_trigger(bc, defenders, predicate):
    """THE ORIGINAL inline loop, reproduced verbatim (the diff's other arm).

    Copied from the trigger that lived at the end of
    ``DefenceManager.update_open_play``, including the local
    ``TACKLE_RADIUS_M`` import (whose value is asserted separately).  If this
    and ``contact_authority.evaluate_contact`` ever disagree, the relocation
    was not behaviour-neutral.
    """
    from world.open_play_engine import TACKLE_RADIUS_M
    for p in defenders:
        if p is bc:
            continue
        if getattr(p, "fullback_role_active", False) and not getattr(
            p, "can_acquire_tackle", False
        ):
            continue
        tackle_role = getattr(p, "tackle_role", None)
        if tackle_role not in ("PRIMARY", "SECONDARY"):
            continue
        dist = math.hypot(bc.x - p.x, bc.y - p.y)
        if dist < TACKLE_RADIUS_M:
            pre_result = predicate(bc, p)
            if pre_result == "MISS":
                continue
            elif pre_result == "ARM_GRAB":
                bc.vx *= 0.85
                bc.vy *= 0.85
                continue
            elif pre_result == "GLANCING":
                lateral_dir = 1.0 if bc.y > p.y else -1.0
                bc.y += 0.5 * lateral_dir
                bc.vy = lateral_dir * 2.0
                continue
            else:
                return p
    return None


def run_authority(world, ope, defenders, bc):
    """Call the authority with the per-frame memo cleared."""
    ca.reset_contact_authority(world)
    return ca.evaluate_contact(world, ope, defenders, bc, TUNERS)
# ─────────────────────────────────────────────────────────────────────
# [1] THE SURFACE
# ─────────────────────────────────────────────────────────────────────

def verify_surface():
    section("[1] The surface - one declared authority, two outcomes")

    check(1, "the authority module exists and is importable",
          ca.__name__ == "game.defence.defence_lvl_1.contact_authority")
    check(1, "exactly TWO outcomes, and they are the declared pair",
          [o.value for o in ca.ContactOutcome]
          == ["CONTACT_FAILED", "CONTACT_FORMED"],
          "got=%s" % [o.value for o in ca.ContactOutcome])

    reasons = [r.value for r in ca.ContactFailureReason]
    for expected in ("no_in_radius_body", "not_tackle_role",
                     "fullback_without_authority", "out_of_radius",
                     "score_miss", "score_arm_grab", "score_glancing",
                     "formed"):
        check(1, "the reason vocabulary declares %r" % expected,
              expected in reasons)

    world = stub_world()
    bc = stub_bc()
    body = stub_body(10, bc.x - 0.5, bc.y)
    ope = ScriptedOPE({10: "CONTACT"})
    decision = run_authority(world, ope, [body], bc)

    check(1, "the call returns exactly one decision object",
          isinstance(decision, ca.ContactDecision))
    check(1, "the decision declares an outcome from the closed pair",
          decision.outcome in ca.ContactOutcome)
    check(1, "CONTACT_FORMED names the winner",
          decision.outcome is ca.ContactOutcome.CONTACT_FORMED
          and decision.winner is body
          and decision.winner_id == 10)
    check(1, "the decision is PUBLISHED on the world (the ownership boundary)",
          ca.last_decision(world) is decision
          and getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0) == 1)
    check(1, "the decision carries explicit formation data per body",
          len(decision.evidence) == 1
          and decision.evidence[0].player_id == 10
          and decision.evidence[0].failed_at
          is ca.ContactFailureReason.FORMED)
    check(1, "the decision records the carrier state it observed",
          (decision.bc_x, decision.bc_y) == (float(bc.x), float(bc.y)))
    check(1, "a formed decision names the manager channel",
          decision.channel == ca.CHANNEL_MANAGER_TRIGGER)

    # Per-frame idempotence: a second call must NOT re-apply carrier effects.
    world2 = stub_world()
    bc2 = stub_bc(vx=10.0, vy=10.0)
    grab = stub_body(11, bc2.x - 0.5, bc2.y)
    ope2 = ScriptedOPE({11: "ARM_GRAB"})
    ca.reset_contact_authority(world2)
    ca.evaluate_contact(world2, ope2, [grab], bc2, TUNERS)
    after_first = (bc2.vx, bc2.vy)
    again = ca.evaluate_contact(world2, ope2, [grab], bc2, TUNERS)
    check(1, "a second call in the same frame does NOT re-apply the effects",
          (bc2.vx, bc2.vy) == after_first and again is ca.last_decision(world2),
          "first=%s second=%s" % (after_first, (bc2.vx, bc2.vy)))

    check(1, "a carrier-less world can never form a contest",
          run_authority(stub_world(), ScriptedOPE(), [], None).outcome
          is ca.ContactOutcome.CONTACT_FAILED)

    report = ca.format_contact_authority_report(world)
    check(1, "the report renders the latest decision",
          isinstance(report, str) and "contact_authority=" in report,
          report[:64])

    # ── THE SINGLE TES-ENTRY CHOKE POINT ──
    # ``enter_tes`` is the ONLY path to TES, so its contract is part of the
    # declared surface: it publishes the formation with the admitting channel
    # and delegates to the engine's own, unchanged entry point.
    world3 = stub_world(frame=42)
    bc3 = stub_bc()
    winner = stub_body(12, bc3.x - 0.4, bc3.y)
    ope3 = ScriptedOPE()
    ca.reset_contact_authority(world3)
    pub = ca.enter_tes(world3, ope3, bc3, winner,
                       ca.CHANNEL_STRUCTURED_ENGAGE)
    check(1, "enter_tes publishes the formation with its channel",
          pub is not None
          and pub.outcome is ca.ContactOutcome.CONTACT_FORMED
          and pub.channel == ca.CHANNEL_STRUCTURED_ENGAGE
          and pub.winner_id == 12)
    check(1, "enter_tes records the contest on the world",
          ca.last_contest(world3) is pub)
    check(1, "enter_tes delegates to the engine's single TES entry",
          ope3.start_calls == [(bc3.player_id, 12)],
          "start_calls=%s" % ope3.start_calls)
    check(1, "the contest record survives a later FAILED decision",
          (ca.last_contest(world3) is pub)
          and ca.last_contest(world3).outcome
          is ca.ContactOutcome.CONTACT_FORMED)

    # The manager channel has ALREADY published this frame's decision, so it
    # must NOT republish (no extra sequence number) but MUST still enter TES.
    world4 = stub_world(frame=43)
    bc4 = stub_bc()
    body4 = stub_body(13, bc4.x - 0.4, bc4.y)
    ope4 = ScriptedOPE({13: "CONTACT"})
    run_authority(world4, ope4, [body4], bc4)
    seq_before = getattr(world4, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0)
    ca.enter_tes(world4, ope4, bc4, body4, ca.CHANNEL_MANAGER_TRIGGER,
                 already_decided=True)
    check(1, "the manager handover does NOT republish the decision",
          getattr(world4, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0) == seq_before
          and ope4.start_calls == [(bc4.player_id, 13)],
          "seq %s -> %s" % (seq_before,
                            getattr(world4, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0)))

    check(1, "the declared channel set is complete and non-empty",
          all(isinstance(getattr(ca, n), str) and getattr(ca, n)
              for n in ("CHANNEL_MANAGER_TRIGGER",
                        "CHANNEL_STRUCTURED_ENGAGE",
                        "CHANNEL_UNIFIED_ENGAGE",
                        "CHANNEL_PURSUIT_ENGAGE",
                        "CHANNEL_LVL1_CHASE_ENGAGE")))
    check(1, "the channel names are distinct",
          len({ca.CHANNEL_MANAGER_TRIGGER, ca.CHANNEL_STRUCTURED_ENGAGE,
               ca.CHANNEL_UNIFIED_ENGAGE, ca.CHANNEL_PURSUIT_ENGAGE,
               ca.CHANNEL_LVL1_CHASE_ENGAGE}) == 5)
# ─────────────────────────────────────────────────────────────────────
# [2] THE CONJUNCTS
# ─────────────────────────────────────────────────────────────────────

def verify_conjuncts():
    section("[2] The five conjuncts, in order, and the first-wins rule")

    # CONJUNCT 1 - a fullback WITHOUT tackle authority is skipped.
    world = stub_world()
    bc = stub_bc()
    fb = stub_body(20, bc.x - 0.5, bc.y, fullback_role_active=True)
    ope = ScriptedOPE({20: "CONTACT"})
    d = run_authority(world, ope, [fb], bc)
    check(2, "a fullback WITHOUT can_acquire_tackle never forms a contest",
          d.outcome is ca.ContactOutcome.CONTACT_FAILED
          and ope.calls == []
          and d.reason is ca.ContactFailureReason.FULLBACK_WITHOUT_AUTHORITY,
          "reason=%s calls=%s" % (d.reason.value, ope.calls))

    world = stub_world()
    bc = stub_bc()
    fb_ok = stub_body(21, bc.x - 0.5, bc.y, fullback_role_active=True,
                      can_acquire_tackle=True)
    ope = ScriptedOPE({21: "CONTACT"})
    d = run_authority(world, ope, [fb_ok], bc)
    check(2, "...WITH can_acquire_tackle it IS admitted (the live conjunct)",
          d.outcome is ca.ContactOutcome.CONTACT_FORMED
          and d.winner is fb_ok)

    # CONJUNCT 2 - only PRIMARY / SECONDARY may engage.
    for role in ("LINE", None, "THIRD", "PASSIVE"):
        world = stub_world()
        bc = stub_bc()
        body = stub_body(22, bc.x - 0.5, bc.y, role=role)
        ope = ScriptedOPE({22: "CONTACT"})
        d = run_authority(world, ope, [body], bc)
        check(2, "tackle_role=%r is NOT a contest candidate" % (role,),
              d.outcome is ca.ContactOutcome.CONTACT_FAILED
              and ope.calls == []
              and d.reason is ca.ContactFailureReason.NOT_TACKLE_ROLE,
              "reason=%s" % d.reason.value)
    for role in ("PRIMARY", "SECONDARY"):
        world = stub_world()
        bc = stub_bc()
        body = stub_body(23, bc.x - 0.5, bc.y, role=role)
        ope = ScriptedOPE({23: "CONTACT"})
        d = run_authority(world, ope, [body], bc)
        check(2, "tackle_role=%r IS a contest candidate" % role,
              d.outcome is ca.ContactOutcome.CONTACT_FORMED)

    # CONJUNCT 3 - the contact radius, sourced from its ORIGINAL owner.
    check(2, "the contact radius comes from world.open_play_engine",
          abs(RADIUS - 1.5) < 1e-12, "radius=%.4f" % RADIUS)
    world = stub_world()
    bc = stub_bc()
    out = stub_body(24, bc.x + RADIUS + 0.01, bc.y)
    ope = ScriptedOPE({24: "CONTACT"})
    d = run_authority(world, ope, [out], bc)
    check(2, "a role holder OUTSIDE the radius never reaches the predicate",
          d.outcome is ca.ContactOutcome.CONTACT_FAILED
          and ope.calls == []
          and d.reason is ca.ContactFailureReason.OUT_OF_RADIUS,
          "reason=%s" % d.reason.value)
    check(2, "...and it is still recorded as explicit formation data",
          len(d.evidence) == 1 and not d.evidence[0].in_radius
          and d.evidence[0].failed_at
          is ca.ContactFailureReason.OUT_OF_RADIUS)

    # CONJUNCT 4 - the four bands, and their frame-level reasons.
    for verdict, expected_reason in (
        ("MISS", ca.ContactFailureReason.SCORE_MISS),
        ("ARM_GRAB", ca.ContactFailureReason.SCORE_ARM_GRAB),
        ("GLANCING", ca.ContactFailureReason.SCORE_GLANCING),
    ):
        world = stub_world()
        bc = stub_bc()
        body = stub_body(25, bc.x - 0.5, bc.y)
        ope = ScriptedOPE({25: verdict})
        d = run_authority(world, ope, [body], bc)
        check(2, "verdict %s does NOT form a contest" % verdict,
              d.outcome is ca.ContactOutcome.CONTACT_FAILED
              and d.reason is expected_reason,
              "reason=%s" % d.reason.value)

    # CONJUNCT 5 - the FIRST success wins, and the scan STOPS there.
    world = stub_world()
    bc = stub_bc()
    first = stub_body(30, bc.x - 0.5, bc.y)
    second = stub_body(31, bc.x - 0.4, bc.y)
    third = stub_body(32, bc.x - 0.3, bc.y)
    ope = ScriptedOPE({30: "CONTACT", 31: "CONTACT", 32: "CONTACT"})
    d = run_authority(world, ope, [first, second, third], bc)
    check(2, "the first qualifying body wins and the scan stops",
          d.winner is first and ope.calls == [30],
          "winner=%s calls=%s" % (d.winner_id, ope.calls))

    # The scan CONTINUES past a non-fatal band (order is load-bearing).
    world = stub_world()
    bc = stub_bc()
    a = stub_body(33, bc.x - 0.5, bc.y)
    b = stub_body(34, bc.x - 0.4, bc.y)
    ope = ScriptedOPE({33: "MISS", 34: "CONTACT"})
    d = run_authority(world, ope, [a, b], bc)
    check(2, "the scan CONTINUES past MISS and finds the later winner",
          d.winner is b and ope.calls == [33, 34],
          "winner=%s calls=%s" % (d.winner_id, ope.calls))

    # An empty frame reports the aggregate reason.
    world = stub_world()
    bc = stub_bc()
    d = run_authority(world, ScriptedOPE(), [], bc)
    check(2, "an empty frame reports NO_IN_RADIUS_BODY",
          d.reason is ca.ContactFailureReason.NO_IN_RADIUS_BODY)

    # The carrier itself is never a candidate.  The live loop skips by
    # IDENTITY (``if p is bc: continue``) -- NOT by ``player_id`` -- so the
    # faithful test puts the carrier OBJECT in the candidate list AND gives it
    # a valid ``tackle_role``, so the check fails if the identity skip is ever
    # weakened to an id comparison or removed.
    world = stub_world()
    bc = stub_bc(pid=99)
    bc.tackle_role = "PRIMARY"
    ope = ScriptedOPE({99: "CONTACT"})
    d = run_authority(world, ope, [bc], bc)
    check(2, "the carrier itself is skipped by IDENTITY, never evaluated",
          d.outcome is ca.ContactOutcome.CONTACT_FAILED and ope.calls == [],
          "calls=%s" % ope.calls)


# ─────────────────────────────────────────────────────────────────────
# [3] DIFFERENTIAL EQUIVALENCE  (the "no behaviour change" proof)
# ─────────────────────────────────────────────────────────────────────

def _build_bodies(specs):
    """specs: (pid, dx, dy, role, fullback, can_acquire)."""
    out = []
    for spec in specs:
        pid, dx, dy, role, fb, can = spec
        out.append(stub_body(
            pid, 60.0 + dx, 35.0 + dy, role=role,
            fullback_role_active=fb, can_acquire_tackle=can,
        ))
    return out


def _equivalence_cases():
    """A deterministic matrix of frames covering every conjunct path."""
    R = RADIUS
    cases = []

    def case(name, specs, verdicts):
        cases.append((name, specs, dict(verdicts)))

    # Empty / role-only frames.
    case("empty frame", [], {})
    case("one LINE body adjacent", [(1, -0.5, 0.0, "LINE", False, False)], {})
    case("one PASSIVE body adjacent",
         [(1, -0.5, 0.0, "PASSIVE", False, False)], {})
    case("body with NO role installed",
         [(1, -0.5, 0.0, None, False, False)], {})
    case("fullback without authority",
         [(1, -0.5, 0.0, "PRIMARY", True, False)], {})
    case("fullback WITH authority",
         [(1, -0.5, 0.0, "PRIMARY", True, True)], {1: "CONTACT"})

    # Radius boundary: inside, exactly ON, just outside.
    case("role holder exactly AT the radius",
         [(1, -R, 0.0, "PRIMARY", False, False)], {1: "CONTACT"})
    case("role holder just inside the radius",
         [(1, -R + 1e-6, 0.0, "PRIMARY", False, False)], {1: "CONTACT"})
    case("role holder just outside the radius",
         [(1, -R - 1e-6, 0.0, "PRIMARY", False, False)], {1: "CONTACT"})
    case("role holder far outside",
         [(1, -25.0, 0.0, "PRIMARY", False, False)], {1: "CONTACT"})
    case("diagonal exactly on the radius",
         [(1, -R / math.sqrt(2.0), -R / math.sqrt(2.0), "PRIMARY",
           False, False)], {1: "CONTACT"})

    # The four bands.
    case("MISS", [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "MISS"})
    case("ARM_GRAB", [(1, -0.5, 0.0, "PRIMARY", False, False)],
         {1: "ARM_GRAB"})
    case("GLANCING", [(1, -0.5, 0.0, "PRIMARY", False, False)],
         {1: "GLANCING"})
    case("CONTACT", [(1, -0.5, 0.0, "PRIMARY", False, False)],
         {1: "CONTACT"})
    case("unknown verdict string falls to CONTACT",
         [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "SOMETHING_ELSE"})

    # Ordering / short-circuit: non-fatal bands then a winner.
    case("MISS then CONTACT",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.4, 0.0, "PRIMARY", False, False)],
         {1: "MISS", 2: "CONTACT"})
    case("ARM_GRAB then CONTACT (carrier damped first)",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.4, 0.0, "PRIMARY", False, False)],
         {1: "ARM_GRAB", 2: "CONTACT"})
    case("GLANCING then CONTACT (carrier displaced first)",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.4, 0.0, "PRIMARY", False, False)],
         {1: "GLANCING", 2: "CONTACT"})
    case("MISS, ARM_GRAB, GLANCING (no contest)",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.4, 0.0, "PRIMARY", False, False),
          (3, -0.3, 0.0, "SECONDARY", False, False)],
         {1: "MISS", 2: "ARM_GRAB", 3: "GLANCING"})
    case("ARM_GRAB then ARM_GRAB (damped twice)",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.45, 0.0, "PRIMARY", False, False)],
         {1: "ARM_GRAB", 2: "ARM_GRAB"})
    case("GLANCING twice (displaced twice)",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.45, 0.0, "PRIMARY", False, False)],
         {1: "GLANCING", 2: "GLANCING"})
    case("first CONTACT wins over a later CONTACT",
         [(1, -0.5, 0.0, "PRIMARY", False, False),
          (2, -0.3, 0.0, "PRIMARY", False, False)],
         {1: "CONTACT", 2: "CONTACT"})

    # Mixed roles and a fullback in the middle of the list.
    case("LINE, fullback, PRIMARY, MISS-then-CONTACT",
         [(1, -0.4, 0.0, "LINE", False, False),
          (2, -0.4, 0.0, "PRIMARY", True, False),
          (3, -0.5, 0.0, "PRIMARY", False, False),
          (4, -0.3, 0.0, "SECONDARY", False, False)],
         {3: "MISS", 4: "CONTACT"})
    case("lateral offsets change GLANCING direction (bc above defender)",
         [(1, -0.5, +0.4, "PRIMARY", False, False)], {1: "GLANCING"})
    case("lateral offsets change GLANCING direction (bc below defender)",
         [(1, -0.5, -0.4, "PRIMARY", False, False)], {1: "GLANCING"})
    case("GLANCING displaces a later body out of radius",
         [(1, -0.5, -0.6, "PRIMARY", False, False),
          (2, -0.5, +0.6, "PRIMARY", False, False)],
         {1: "GLANCING", 2: "CONTACT"})

    return cases


def verify_equivalence():
    section("[3] Differential equivalence vs the ORIGINAL loop (behaviour)")

    # -- SCOPE OF THE BYTE-IDENTITY CLAIM (2026-10-03) --
    # This section's claim is about the RELOCATION: with the CONTACT_SPACE_MISS
    # path suppressed, the authority must be byte-identical to the original
    # loop it replaced.  The CONTACT_SPACE_MISS path is a DELIBERATE behaviour
    # change, requested for MISS resolutions whose bodies are inside the
    # combined body radius; asserting byte-identity with it armed would assert
    # the opposite of the requirement.  Section [3b] asserts the complementary
    # property: with the path ARMED, the ONLY divergence is exactly the
    # contact-space MISS cases and an ordinary MISS is still untouched.
    verify_equivalence._real_csm = ca._apply_contact_space_miss
    ca._apply_contact_space_miss = lambda *a, **k: None

    cases = _equivalence_cases()
    mismatches = []

    for name, specs, verdicts in cases:
        # ── ARM A: the original inline loop ──
        bc_a = stub_bc()
        bodies_a = _build_bodies(specs)
        ope_a = ScriptedOPE(verdicts)
        winner_a = legacy_trigger(bc_a, bodies_a, ope_a._check_pre_tackle)
        state_a = (bc_a.x, bc_a.y, bc_a.vx, bc_a.vy, ope_a.calls,
                   None if winner_a is None
                   else getattr(winner_a, "player_id", None))

        # ── ARM B: the declared Contact Authority ──
        bc_b = stub_bc()
        bodies_b = _build_bodies(specs)
        ope_b = ScriptedOPE(verdicts)
        world_b = stub_world(frame=1000 + len(RESULTS))
        decision = run_authority(world_b, ope_b, bodies_b, bc_b)
        state_b = (bc_b.x, bc_b.y, bc_b.vx, bc_b.vy, ope_b.calls,
                   decision.winner_id)

        if state_a != state_b:
            mismatches.append((name, state_a, state_b))

    check(3, "with CONTACT_SPACE_MISS suppressed, every case produces "
             "IDENTICAL carrier state, predicate order and winner "
             "(%d cases)" % len(cases),
          not mismatches,
          "mismatches=%s" % mismatches[:3])

    # The equivalence must be EXACT float arithmetic, not "close enough".
    exact_detail = ""
    exact_ok = True
    for name, specs, verdicts in cases:
        if not any(v == "ARM_GRAB" for v in verdicts.values()):
            continue
        bc_a = stub_bc(vx=7.3, vy=-2.9)
        legacy_trigger(bc_a, _build_bodies(specs), ScriptedOPE(verdicts)
                       ._check_pre_tackle)
        bc_b = stub_bc(vx=7.3, vy=-2.9)
        run_authority(stub_world(frame=2000 + len(RESULTS)),
                      ScriptedOPE(verdicts), _build_bodies(specs), bc_b)
        if (bc_a.vx, bc_a.vy) != (bc_b.vx, bc_b.vy):
            exact_ok = False
            exact_detail = "%s: %s vs %s" % (name, (bc_a.vx, bc_a.vy),
                                             (bc_b.vx, bc_b.vy))
            break
    check(3, "the ARM_GRAB damping is BIT-IDENTICAL (same float multiply)",
          exact_ok, exact_detail)

    # The GLANCING displacement must also be exact, including the sign choice.
    glance_ok = True
    glance_detail = ""
    for dy in (+0.4, -0.4, 0.0):
        bc_a = stub_bc(vy=1.7)
        legacy_trigger(bc_a, _build_bodies(
            [(1, -0.5, dy, "PRIMARY", False, False)]),
            ScriptedOPE({1: "GLANCING"})._check_pre_tackle)
        bc_b = stub_bc(vy=1.7)
        run_authority(stub_world(frame=2500 + len(RESULTS)),
                      ScriptedOPE({1: "GLANCING"}),
                      _build_bodies([(1, -0.5, dy, "PRIMARY", False, False)]),
                      bc_b)
        if (bc_a.y, bc_a.vy) != (bc_b.y, bc_b.vy):
            glance_ok = False
            glance_detail = "dy=%s: %s vs %s" % (
                dy, (bc_a.y, bc_a.vy), (bc_b.y, bc_b.vy))
            break
    check(3, "the GLANCING displacement is BIT-IDENTICAL (offset and sign)",
          glance_ok, glance_detail)

    # Coverage proof: the matrix actually exercises BOTH outcomes and all
    # four bands (otherwise the equivalence would prove nothing).
    formed = 0
    failed = 0
    bands = set()
    for name, specs, verdicts in cases:
        bc = stub_bc()
        d = run_authority(stub_world(frame=3000 + len(RESULTS)),
                          ScriptedOPE(verdicts), _build_bodies(specs), bc)
        if d.outcome is ca.ContactOutcome.CONTACT_FORMED:
            formed += 1
        else:
            failed += 1
        for row in d.evidence:
            bands.add(row.predicate_verdict)
    check(3, "the matrix exercises BOTH outcomes",
          formed > 0 and failed > 0, "formed=%d failed=%d" % (formed, failed))
    check(3, "the matrix exercises every band plus the winner",
          {"MISS", "ARM_GRAB", "GLANCING", "CONTACT"} <= bands,
          "bands=%s" % sorted(b for b in bands if b))

    # The predicate is consulted in caller-list order — the same order the
    # original used, which is what makes the GLANCING displacement observable.
    world = stub_world()
    bc = stub_bc()
    bodies = _build_bodies([
        (1, -0.5, 0.0, "PRIMARY", False, False),
        (2, -0.4, 0.0, "PRIMARY", False, False),
        (3, -0.3, 0.0, "PRIMARY", False, False),
    ])
    ope = ScriptedOPE({1: "MISS", 2: "MISS", 3: "CONTACT"})
    d = run_authority(world, ope, bodies, bc)
    check(3, "candidates are consulted in caller-list order",
          ope.calls == [1, 2, 3], "calls=%s" % ope.calls)

    # Restore the real CONTACT_SPACE_MISS path (section [3b] needs it armed).
    ca._apply_contact_space_miss = verify_equivalence._real_csm


def _cases_with_contact_space_miss():
    """Matrix cases in which a scripted MISS lands INSIDE contact space.

    Uses the SAME predicate the decider uses, evaluated at the declaration
    geometry.  The stub bodies carry no CollisionBody, so both radii fall back
    to the declared default and the threshold is 2 x 0.55 = 1.10 m.
    """
    from game.physics.contact_space_miss import is_contact_space_miss
    out = set()
    for name, specs, verdicts in _equivalence_cases():
        bc = stub_bc()
        for body in _build_bodies(specs):
            pid = getattr(body, "player_id", None)
            if verdicts.get(pid) != "MISS":
                continue
            if (getattr(body, "fullback_role_active", False)
                    and not getattr(body, "can_acquire_tackle", False)):
                continue
            if getattr(body, "tackle_role", None) not in ("PRIMARY",
                                                         "SECONDARY"):
                continue
            if math.hypot(bc.x - body.x, bc.y - body.y) >= RADIUS:
                continue
            if is_contact_space_miss(body, bc):
                out.add(name)
    return out


def verify_contact_space_miss_clause():
    """[3b] With the path ARMED, the ONLY divergence is the contact-space MISS.

    The machine-checked statement of the CONTACT_SPACE_MISS clause: a MISS
    whose bodies are inside the combined body radius NOW mutates both bodies
    (by design); every other frame in the SAME matrix stays byte-identical to
    the original loop.
    """
    section("[3b] CONTACT_SPACE_MISS is the ONLY divergence (2026-10-03)")

    from game.physics import contact_space_miss as _csm

    expected = _cases_with_contact_space_miss()
    divergent = set()
    teleports = []
    over_cap = []
    non_identical = []
    events_seen = 0
    cap = _csm.VELOCITY_DELTA_MAX_MPS

    for name, specs, verdicts in _equivalence_cases():
        bc_a = stub_bc()
        bodies_a = _build_bodies(specs)
        ope_a = ScriptedOPE(verdicts)
        winner_a = legacy_trigger(bc_a, bodies_a, ope_a._check_pre_tackle)
        state_a = (bc_a.x, bc_a.y, bc_a.vx, bc_a.vy, ope_a.calls,
                   None if winner_a is None
                   else getattr(winner_a, "player_id", None))

        bc_b = stub_bc()
        bodies_b = _build_bodies(specs)
        ope_b = ScriptedOPE(verdicts)
        decision = run_authority(stub_world(frame=4000 + len(RESULTS)),
                                 ope_b, bodies_b, bc_b)
        state_b = (bc_b.x, bc_b.y, bc_b.vx, bc_b.vy, ope_b.calls,
                   decision.winner_id)
        events = tuple(
            getattr(decision, "contact_space_miss_events", ()) or ()
        )
        reacted = bool(events) and all(
            getattr(ev, "applied", False) for ev in events
        )
        # THE ARC MODEL (2026-10-04).  The reaction is a scripted ballistic arc
        # integrated by Player.update, so the authority writes NO POSITION at
        # declaration time: the published (x, y), the predicate calls and the
        # winner must equal the pre-change loop's in EVERY case, and THAT
        # equality is the no-teleport guarantee.
        #
        # It DOES write the carrier's declared, capped speed loss at declaration
        # time -- that is the design -- so the VELOCITY is compared by its
        # declared BOUND rather than by equality, while a case that records NO
        # reaction must still be byte-identical, velocity included.
        if (state_a[0], state_a[1]) != (state_b[0], state_b[1]) \
                or state_a[4] != state_b[4] or state_a[5] != state_b[5]:
            teleports.append(name)
        dvx = state_b[2] - state_a[2]
        dvy = state_b[3] - state_a[3]
        delta = (dvx * dvx + dvy * dvy) ** 0.5
        if reacted:
            divergent.add(name)
            if delta > cap + 1e-9:
                over_cap.append((name, delta))
        elif delta > 1e-12:
            non_identical.append((name, delta))
        events_seen += len(events)

    check(3, "the matrix DOES exercise the new path (events recorded)",
          events_seen > 0, "events=%d" % events_seen)
    check(3, "NO TELEPORT: position, predicate calls and winner are unchanged "
             "in EVERY case, the reaction fires EXACTLY for the contact-space "
             "MISS cases, and every carrier delta is within the declared cap",
          not teleports and divergent == expected
          and not over_cap and not non_identical,
          "teleports=%s divergent=%s expected=%s over_cap=%s untouched=%s"
          % (sorted(teleports), sorted(divergent), sorted(expected),
             over_cap, non_identical))

    # An ordinary MISS: inside the CONTACT RADIUS but OUTSIDE contact space.
    specs = [(1, -1.30, 0.0, "PRIMARY", False, False)]
    bc_a = stub_bc()
    legacy_trigger(bc_a, _build_bodies(specs),
                   ScriptedOPE({1: "MISS"})._check_pre_tackle)
    bc_b = stub_bc()
    d = run_authority(stub_world(frame=4500 + len(RESULTS)),
                      ScriptedOPE({1: "MISS"}), _build_bodies(specs), bc_b)
    check(3, "an ordinary MISS (outside contact space) is byte-identical "
             "and records NO event",
          (bc_a.x, bc_a.y, bc_a.vx, bc_a.vy)
          == (bc_b.x, bc_b.y, bc_b.vx, bc_b.vy)
          and len(getattr(d, "contact_space_miss_events", ()) or ()) == 0)
# ─────────────────────────────────────────────────────────────────────
# [4] EVIDENCE PROVIDERS
# ─────────────────────────────────────────────────────────────────────

def verify_evidence_providers():
    section("[4] The writers are evidence providers, not deciders")

    providers = ca.declared_evidence_providers()
    names = " ".join("%s %s %s" % row for row in providers)
    for writer in ("intercept_tacklers.select_lane_tacklers",
                   "pre_tackle_authority.apply_pre_tackle",
                   "pretackle_corridor.enter_corridor",
                   "pretackle_machine.run_machine",
                   "world.open_play_engine._check_pre_tackle"):
        check(4, "the declared provider set names %s" % writer,
              writer in names)
    check(4, "the declared set also names the engage-guard, the retreat "
             "obligation and the slot conjunct",
          "tackle_engage_guard.is_tackle_engage_guard" in names
          and "retreat_obligation.is_retreat_obligated" in names
          and "slot_arbitration.is_slot_tackle_eligible" in names,
          "%d providers" % len(providers))

    # Every declared writer module still exists and still defines its entry.
    for label, path in WRITER_PATHS.items():
        src = read_src(path)
        check(4, "%s still exists and is non-empty" % label, len(src) > 0)
    check(4, "intercept_tacklers still defines select_lane_tacklers",
          "def select_lane_tacklers(" in read_src(WRITER_PATHS[
              "intercept_tacklers"]))
    check(4, "pre_tackle_authority still defines apply_pre_tackle",
          "def apply_pre_tackle(" in read_src(WRITER_PATHS[
              "pre_tackle_authority"]))
    check(4, "pretackle_corridor still defines enter_corridor",
          "def enter_corridor(" in read_src(WRITER_PATHS[
              "pretackle_corridor"]))
    check(4, "pretackle_machine still defines run_machine",
          "def run_machine(" in read_src(WRITER_PATHS[
              "pretackle_machine"]))

    # The authority CONSUMES the writers' evidence.  Two forms are legitimate
    # and both are asserted:
    #   (a) the stamps a writer PUBLISHES ON A BODY, read directly --
    #       tackle_role, depth_class, pre_tackle_state, _machine_state;
    #   (b) the writers' DECLARED PREDICATES, which is how an evidence source
    #       that owns a PRIVATE latch must be consulted.  The corridor is the
    #       case in point: it is read as is_corridor_owned() /
    #       corridor_frames(), never as a bare `_pta_corridor` field, so the
    #       authority stays on the corridor's published API.
    src = read_src(MODULE_PATH)
    refs = code_names(src)
    for token in ("tackle_role", "depth_class", "pre_tackle_state",
                  "_machine_state"):
        check(4, "the authority reads the '%s' published stamp" % token,
              token in refs)
    for token in ("is_slot_tackle_eligible", "is_tackle_engage_guard",
                  "is_corridor_owned", "corridor_frames",
                  "is_retreat_obligated"):
        check(4, "the authority consumes the declared %s() predicate" % token,
              token in refs)
    check(4, "the corridor is consumed through its declared API only",
          "_pta_corridor" not in refs,
          "no raw private-latch read")

    # ...and never WRITES them.  AST STORES ONLY, so a local named after a
    # stamp is not confused with writing the stamp.
    stores = attribute_stores(src)
    written_attrs = {attr for _base, attr, _line in stores}
    writer_outputs = {
        "tackle_role", "pre_tackle_state", "_pta_corridor",
        "_pta_corridor_frames", "_machine_state", "_machine_outcome",
        "depth_class", "depth_side", "target_x", "target_y",
        "movement_state", "frozen", "freeze_timer", "tes_active",
        "tackler_ids", "primary_tackler_id",
    }
    bad = sorted(written_attrs & writer_outputs)
    check(4, "the authority never STORES any writer's output",
          not bad, "offenders=%s" % bad)

    # THE STRONGEST FORM OF THIS CLAIM: the module writes to NOTHING except
    # the ball carrier's declared failed-contact effects.  Every attribute
    # store in the whole module must target ``bc``.
    non_carrier = sorted({"<expr>.%s" % attr
                          for base, attr, _line in stores if base != "bc"})
    check(4, "EVERY attribute store in the module targets the carrier",
          not non_carrier, "offenders=%s" % non_carrier)

    check(4, "the ONLY carrier attributes written are the declared effects",
          written_attrs <= {"vx", "vy", "y", "x"},
          "writes=%s" % sorted(written_attrs))


# ─────────────────────────────────────────────────────────────────────
# [5] TES ENTRY PATH
# ─────────────────────────────────────────────────────────────────────

def _engine_sources(exclude=()):
    """Yield ``(path, source)`` for EVERY engine ``.py`` in the repo.

    REPO-WIDE by construction: it walks ``.`` and skips only the declared
    historical/scratch directories (``SCAN_SKIP_DIRS``) plus the Stage 1B gate
    scripts (``GATE_SCRIPTS``) and any caller-supplied *exclude* path.  Only
    files with an IMPORTABLE module name are yielded (``_is_engine_module``),
    so a ``.bak.py`` backup cannot enter the scan.
    """
    excluded = {os.path.normpath(p) for p in exclude}
    excluded |= {os.path.normpath(g) for g in GATE_SCRIPTS}
    for dirpath, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
        for fn in files:
            if not _is_engine_module(fn):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.normpath(path) in excluded:
                continue
            yield path, read_src(path)


def _unparseable_paths(paths=None):
    """Scanned files that are NOT parseable Python, repo-wide.

    REPORTED, not ignored.  The tolerance in ``call_sites`` exists for scratch
    files whose body is prose, so the verifier asserts that no file under an
    engine package is ever among them.
    """
    bad = []
    for path, text in (paths if paths is not None else _engine_sources()):
        try:
            ast.parse(text)
        except SyntaxError:
            bad.append(os.path.normpath(path))
    return sorted(bad)


def _disabled_dead_ranges(src):
    """Return ``(dead_ranges, referenced_names)`` for a module's copies.

    The engine keeps DEAD historical copies of replaced handlers under the
    ``_disabled_`` prefix.  ``world/open_play_engine.py`` is the case in point:
    ``_disabled_update_defence_impl`` is a verbatim copy of the defence
    dispatch that the manager now owns, INCLUDING its own inline FIRE loop and
    its own ``_start_tackle`` call.  It is not reachable engine code, so a
    TES-entry scan must not read its call as a live entry point.

    Exempting such a copy is only sound if it really is DEAD, which is what
    ``referenced_names`` proves: a ``_disabled_`` name mentioned anywhere in
    the module (a call, an attribute, a ``getattr`` string) is returned there
    and the caller must REJECT the scan rather than exempt it.  That way the
    exemption can never hide a live path behind a name.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], []

    defs = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name.startswith("_disabled_")):
            defs.append((node.lineno, node.end_lineno or node.lineno,
                         node.name))

    names = {name for _start, _end, name in defs}
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            referenced.add(node.attr)

    dead = [(start, end) for start, end, name in defs
            if name not in referenced]
    return dead, sorted(referenced)


def verify_tes_entry_path():
    section("[5] TES entry has EXACTLY ONE reachable choke point")

    src = read_src(MODULE_PATH)
    manager = read_src(MANAGER_PATH)
    engine = read_src(ENGINE_PATH)
    tes = read_src(TES_PATH)

    # The authority's CODE still never reaches into TES internals.  Checked
    # against the AST, not the raw text: the docstring legitimately NAMES the
    # entry point it defers to.
    code_refs = code_names(src)
    for token in ("tes_start_tackle", "TACKLE_INIT", "set_phase",
                  "tackler_ids", "primary_tackler_id", "tes_ctx",
                  "TESPhase"):
        check(5, "the authority's CODE never references %r" % token,
              token not in code_refs)

    # ── THE INVARIANT: one choke point, engine-wide ──
    # ``_start_tackle`` is the engine's documented single TES entry.  Exactly
    # one module may CALL it: the declared Contact Authority (once on the
    # engage channel, once on the manager-channel handover).  Every other
    # engine module that can admit a contest must route through ``enter_tes``.
    #
    # This is the check whose ABSENCE let the first Stage 1B shipment declare a
    # single owner while the behaviour pipeline still entered TES directly.
    authority_calls = call_sites(src, "_start_tackle")
    check(5, "the authority calls _start_tackle exactly twice "
             "(engage channel + manager handover)",
          len(authority_calls) == 2, "lines=%s" % authority_calls)

    # THE SCAN MUST BE NON-VACUOUS.  A repo-wide walk whose skip list silently
    # ate the engine would pass this section while proving nothing.
    scanned = list(_engine_sources())
    check(5, "the TES-entry scan really is repo-wide (%d engine modules)"
             % len(scanned), len(scanned) > 200,
          "a walk that skipped the tree would report a handful")
    check(5, "the scan reaches the manager and the engage modules",
          any(p.replace("\\", "/").endswith("managers/defence_manager.py")
              for p, _s in scanned)
          and any(p.replace("\\", "/").endswith(
              "defensive_behaviour_unified.py") for p, _s in scanned)
          and any(p.replace("\\", "/").endswith(
              "contact_authority.py") for p, _s in scanned))
    check(5, "the importable-module rule is doing real work",
          not _is_engine_module("defence_manager.taskxiv.bak.py")
          and _is_engine_module("defence_manager.py")
          and _is_engine_module("verify_x.py"),
          "a .bak backup must not count as engine code")

    # A repo-wide scan reaches scratch .py files whose body is prose.  The
    # tolerance that keeps the scan alive must NOT be able to hide engine
    # code, so every file under an engine package is asserted to parse.
    unparseable = _unparseable_paths(scanned)
    engine_prefixes = ("game", "world", "player", "ball", "kick_off")
    bad_engine = [p for p in unparseable
                  if p.replace("\\", "/").split("/")[0] in engine_prefixes]
    check(5, "every ENGINE file parses Python; the %d non-parsing files are "
             "all scratch" % len(unparseable),
          not bad_engine,
          "engine files that do not parse: %s" % bad_engine[:5])

    offenders = []
    exempt = []
    for path, text in _engine_sources(exclude=(MODULE_PATH,)):
        sites = call_sites(text, "_start_tackle")
        dead_ranges, referenced = _disabled_dead_ranges(text)
        if referenced:
            offenders.append(
                "%s: reachable _disabled_* definition %s"
                % (path, referenced)
            )
        if not sites:
            continue
        live = [ln for ln in sites
                if not any(a <= ln <= b for a, b in dead_ranges)]
        if live:
            offenders.append("%s:%s" % (path, live))
        else:
            exempt.append((path, sites))

    check(5, "NO reachable engine module calls _start_tackle",
          not offenders, "offenders=%s" % sorted(offenders))
    check(5, "the exempt _start_tackle sites lie only inside UNREACHABLE "
             "_disabled_* copies",
          len(exempt) == 1
          and os.path.normpath(exempt[0][0]) == os.path.normpath(ENGINE_PATH),
          "exempt=%s" % sorted(path for path, _sites in exempt))

    # Every contest-admitting module reaches TES through ``enter_tes``.
    for label, path in ENGAGE_PATHS.items():
        text = read_src(path)
        check(5, "%s routes its TES entry through enter_tes" % label,
              not call_sites(text, "_start_tackle") and "enter_tes" in text,
              "direct calls=%s" % call_sites(text, "_start_tackle"))
    check(5, "the manager routes its TES entry through enter_tes",
          not call_sites(manager, "_start_tackle")
          and "enter_tes(" in manager)

    # The MANAGER is the consumer of the declared decision.
    check(5, "the manager calls the declared authority",
          "evaluate_contact(" in manager)
    check(5, "the manager consumes the declared outcome, not a private test",
          "ContactOutcome.CONTACT_FORMED" in manager
          and "_contact_decision.outcome" in manager)
    check(5, "the manager hands the winner over with the manager channel",
          "enter_tes(world, ope, bc, _contact_decision.winner" in manager
          and "CHANNEL_MANAGER_TRIGGER" in manager)

    # The decider really MOVED: the inline predicate call is gone.
    check(5, "the manager no longer calls the contact predicate itself",
          "pre_result = ope._check_pre_tackle" not in manager,
          "the inline FIRE decision is gone from the manager")

    # The engine's TES entry is intact and unchanged in shape.
    check(5, "_start_tackle still has its original signature",
          "def _start_tackle(self, bc: Player, d: Player)" in engine)
    check(5, "_start_tackle still delegates to tes_start_tackle",
          "ctx = tes_start_tackle(bc, d, self.world)" in engine)
    check(5, "tes_start_tackle is still the declared single TES entry",
          "def tes_start_tackle(bc, defender, world)" in tes)

    # The contact predicate still lives in its own module (Stage 1B did not
    # move it), and the manager still reaches the boundary exactly once.
    check(5, "the contact predicate still lives in world/open_play_engine",
          "def _check_pre_tackle(self, bc, defender)" in engine)
    check(5, "the boundary is evaluated exactly once per frame in the manager",
          manager.count("evaluate_contact(") == 1,
          "count=%d" % manager.count("evaluate_contact("))
# ─────────────────────────────────────────────────────────────────────
# [6] NO BEHAVIOUR CHANGE
# ─────────────────────────────────────────────────────────────────────

def verify_no_behaviour_change():
    section("[6] No behaviour change - owners, thresholds and gates untouched")

    engine = read_src(ENGINE_PATH)
    manager = read_src(MANAGER_PATH)
    src = read_src(MODULE_PATH)

    # The predicate's thresholds are EXACTLY where they were, with the values.
    for literal in ("MISS_THRESHOLD = 0.3", "ARM_GRAB_THRESHOLD = 0.6",
                    "GLANCING_THRESHOLD = 0.75"):
        check(6, "the predicate still declares %r" % literal,
              literal in engine)
    check(6, "the score formula is untouched",
          "tackle_score = angle_score * 0.6 + speed_score * 0.4" in engine)
    moved = [n for n in ("MISS_THRESHOLD", "ARM_GRAB_THRESHOLD",
                         "GLANCING_THRESHOLD") if n in code_names(src)]
    check(6, "the thresholds did NOT move into the authority",
          not moved,
          "offenders=%s (the predicate keeps them in its own module)" % moved)

    # The contact radius still has ONE owner (the engine module).
    check(6, "the engine still owns TACKLE_RADIUS_M",
          "TACKLE_RADIUS_M: float = 1.5" in engine)
    check(6, "the authority reads the radius from that owner",
          "from world.open_play_engine import TACKLE_RADIUS_M" in src)
    check(6, "the authority does NOT take the radius from a tuner",
          "tackle_radius_m" not in src)
    check(6, "the manager no longer imports the radius locally",
          "from world.open_play_engine import TACKLE_RADIUS_M" not in manager)

    # A tuner is NOT a new authority over contest FORMATION.  DefenceTuners
    # legitimately carries PRE-EXISTING physical-contact damping knobs
    # (bc_contact_gain_mps / def_contact_drag_mps / defender_contact_damp):
    # those are collision parameters, not formation policy, and Stage 1B
    # neither reads nor adds them.
    tuner_attrs = [a for a in dir(TUNERS)
                   if "formation" in a.lower() or "contest" in a.lower()
                   or "contact_authority" in a.lower()]
    check(6, "no contest-formation tuner was introduced",
          not tuner_attrs, "attrs=%s" % tuner_attrs)
    check(6, "the pre-existing contact-damping tuners are untouched",
          all(hasattr(TUNERS, a) for a in ("bc_contact_gain_mps",
                                           "def_contact_drag_mps",
                                           "defender_contact_damp")),
          "collision knobs, not formation policy")
    check(6, "DefenceTuners was not modified for Stage 1B",
          "contact_authority" not in read_src(TUNERS_PATH))

    # The effects' magnitudes are the ORIGINAL literals, unmoved.
    check(6, "the arm-grab damping is the original 0.85",
          abs(ca.ARM_GRAB_DAMPING_FACTOR - 0.85) < 1e-12)
    check(6, "the glancing displacement is the original 0.5 m",
          abs(ca.GLANCING_LATERAL_OFFSET_M - 0.5) < 1e-12)
    check(6, "the glancing lateral velocity is the original 2.0 m/s",
          abs(ca.GLANCING_LATERAL_VELOCITY_MPS - 2.0) < 1e-12)

    # NO BEHAVIOURAL CANARY - the deliberate design decision.
    public = [n for n in dir(ca)
              if not n.startswith("_") and callable(getattr(ca, n))]
    gates = [n for n in public if n.endswith("_enabled")
             or n.endswith("_active") or n.endswith("_switch")]
    check(6, "the ONLY gate is the audit sink (no restored second decider)",
          gates == ["audit_enabled"], "gates=%s" % sorted(gates))
    check(6, "the audit gate is an env var, not a gameplay switch",
          isinstance(ca.AUDIT_ENV_VAR, str) and ca.AUDIT_ENV_VAR
          and "enable" not in ca.AUDIT_ENV_VAR.lower(),
          "env=%s" % ca.AUDIT_ENV_VAR)
    check(6, "the only gate is the AUDIT sink, and it is OFF by default",
          ca.audit_enabled() is False)
    check(6, "the authority does not consult the tuner object at all",
          "tuners." not in src.replace("tuners=None", "")
          .replace("tuners for signature", ""))

    # Disabling the audit cannot change a decision: the same frame with the
    # sink off and on produces the SAME outcome, reason and counts.
    specs = [(1, -0.5, 0.0, "PRIMARY", False, False),
             (2, -0.4, 0.0, "PRIMARY", False, False)]
    verdicts = {1: "MISS", 2: "CONTACT"}

    d_off = run_authority(stub_world(frame=9001), ScriptedOPE(verdicts),
                          _build_bodies(specs), stub_bc())
    prev = os.environ.get(ca.AUDIT_ENV_VAR)
    os.environ[ca.AUDIT_ENV_VAR] = "1"
    try:
        d_on = run_authority(stub_world(frame=9002), ScriptedOPE(verdicts),
                             _build_bodies(specs), stub_bc())
    finally:
        if prev is None:
            os.environ.pop(ca.AUDIT_ENV_VAR, None)
        else:
            os.environ[ca.AUDIT_ENV_VAR] = prev
    check(6, "arming the audit sink does NOT change the decision",
          (d_off.outcome, d_off.reason, d_off.winner_id,
           d_off.in_radius_count, d_off.role_holder_count)
          == (d_on.outcome, d_on.reason, d_on.winner_id,
              d_on.in_radius_count, d_on.role_holder_count))


# ─────────────────────────────────────────────────────────────────────
# [7] THE AUDIT
# ─────────────────────────────────────────────────────────────────────

def verify_audit():
    section("[7] The audit names exactly why a contest formed or failed")

    check(7, "the audit sink is OFF by default", ca.audit_enabled() is False)

    if os.path.exists(AUDIT_CSV):
        os.remove(AUDIT_CSV)

    plans = [
        ("formed", [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "CONTACT"}),
        ("miss", [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "MISS"}),
        ("grab", [(1, -0.5, 0.0, "PRIMARY", False, False)], {1: "ARM_GRAB"}),
        ("glance", [(1, -0.5, 0.0, "PRIMARY", False, False)],
         {1: "GLANCING"}),
        ("no_role", [(1, -0.5, 0.0, "LINE", False, False)], {}),
        ("out_of_radius", [(1, -9.0, 0.0, "PRIMARY", False, False)],
         {1: "CONTACT"}),
        ("fullback", [(1, -0.5, 0.0, "PRIMARY", True, False)], {}),
        ("empty", [], {}),
    ]

    prev = os.environ.get(ca.AUDIT_ENV_VAR)
    os.environ[ca.AUDIT_ENV_VAR] = "1"
    try:
        expected_reasons = []
        for i, (label, specs, verdicts) in enumerate(plans):
            d = run_authority(stub_world(frame=7000 + i),
                              ScriptedOPE(verdicts),
                              _build_bodies(specs), stub_bc())
            expected_reasons.append(d.reason.value)
    finally:
        if prev is None:
            os.environ.pop(ca.AUDIT_ENV_VAR, None)
        else:
            os.environ[ca.AUDIT_ENV_VAR] = prev

    check(7, "the CSV sink was written", os.path.exists(AUDIT_CSV))

    rows = []
    with open(AUDIT_CSV, "r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    frame_rows = [r for r in rows if r["row_kind"] == "frame"]
    body_rows = [r for r in rows if r["row_kind"] == "body"]

    check(7, "one frame row per decision",
          len(frame_rows) == len(plans),
          "frames=%d plans=%d" % (len(frame_rows), len(plans)))
    check(7, "every frame row carries a declared reason",
          all(r["reason"] in [x.value for x in ca.ContactFailureReason]
              for r in frame_rows))
    check(7, "the frame reasons match the delivered decisions",
          [r["reason"] for r in frame_rows] == expected_reasons,
          "got=%s" % [r["reason"] for r in frame_rows])
    check(7, "every frame row carries a declared outcome",
          all(r["outcome"] in [x.value for x in ca.ContactOutcome]
              for r in frame_rows))
    check(7, "every body row names the conjunct it failed at",
          body_rows and all(
              r["failed_at"] in [x.value for x in ca.ContactFailureReason]
              for r in body_rows), "body_rows=%d" % len(body_rows))
    check(7, "body rows carry the explicit formation evidence",
          all(r["defender_role"] for r in body_rows)
          and all(r["dist"] != "" for r in body_rows))
    check(7, "formed + failed frame rows reconcile to the frames evaluated",
          sum(1 for r in frame_rows
              if r["outcome"] == ca.ContactOutcome.CONTACT_FORMED.value) == 1
          and sum(1 for r in frame_rows
                  if r["outcome"]
                  == ca.ContactOutcome.CONTACT_FAILED.value) == len(plans) - 1,
          "formed=%d" % sum(1 for r in frame_rows
                            if r["outcome"]
                            == ca.ContactOutcome.CONTACT_FORMED.value))
    check(7, "the failed-contact effects are declared in the audit",
          any(r["arm_grab_damping"] == "1" for r in frame_rows)
          and any(r["glancing_displacement"] not in ("", None)
                  for r in frame_rows))
    check(7, "the audit carries the admitting channel on every frame row",
          all("channel" in r for r in frame_rows)
          and any(r["channel"] for r in frame_rows),
          "channels=%s" % sorted({r.get("channel", "") for r in frame_rows}))

    # The sink is read-only with respect to the engine: nothing is required to
    # consume it, and the file is the only artefact.
    os.remove(AUDIT_CSV)
    check(7, "the audit artefact is removable and nothing depends on it",
          not os.path.exists(AUDIT_CSV))
# ─────────────────────────────────────────────────────────────────────
# [8] IMPORT SAFETY + NO INVENTED STATE
# ─────────────────────────────────────────────────────────────────────

FORBIDDEN_PREFIXES = ("stamp_", "clear_", "arm_", "latch_")
FORBIDDEN_NAMES = (
    "CONTACT_ACTIVE_ATTR",
    "CONTACT_LATCH_ATTR",
    "CONTACT_COOLDOWN_FRAMES",
    "CONTACT_HYSTERESIS_M",
    "CONTACT_TARGET_ATTR",
)
FORBIDDEN_STORE_ATTRS = (
    "contact_active",
    "contact_pending",
    "contact_cooldown",
    "contact_target",
    "contact_frames",
)


def verify_import_safety_and_state():
    section("[8] Import safety + no invented state")

    src = read_src(MODULE_PATH)
    tree = ast.parse(src)

    module_scope_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_scope_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_scope_imports.append(node.module or "")
    allowed = ("__future__", "csv", "dataclasses", "enum", "math", "os",
               "typing")
    offenders = [m for m in module_scope_imports
                 if not m.split(".")[0] in allowed]
    check(8, "module-scope imports are stdlib only (no import cycle)",
          not offenders, "offenders=%s" % offenders)

    # The engine import must be FUNCTION-LOCAL: the engine imports the manager
    # at module scope, so a top-level reach would close a cycle.
    top_level_engine = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("world."):
                top_level_engine = True
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("world."):
                    top_level_engine = True
    check(8, "the engine reach is FUNCTION-LOCAL only", not top_level_engine)
    check(8, "the manager imports the authority at module scope",
          "from game.defence.defence_lvl_1.contact_authority import"
          in read_src(MANAGER_PATH))

    # No formation API under a stamp/clear/arm/latch name: those are the
    # state-writer idioms this module deliberately does NOT own.
    def_names = {node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bad_prefix = sorted(n for n in def_names
                        if n.startswith(FORBIDDEN_PREFIXES))
    check(8, "no stamp_/clear_/arm_/latch_ formation API is defined",
          not bad_prefix, "offenders=%s" % bad_prefix)

    # No latch / cooldown / target-store NAME exists in the module at all.
    declared = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    declared.add(tgt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                declared.add(node.target.id)
    bad_names = sorted(declared & set(FORBIDDEN_NAMES) | set())
    check(8, "no latch / cooldown / target-store name is defined",
          not bad_names, "offenders=%s" % bad_names)

    # ...and nothing STORES such an attribute on any object.
    stores = attribute_stores(src)
    bad_stores = sorted({attr for _b, attr, _l in stores}
                        & set(FORBIDDEN_STORE_ATTRS))
    check(8, "no Store to a latch / cooldown / target-store attribute",
          not bad_stores, "offenders=%s" % bad_stores)

    # DETERMINISM: identical inputs, identical verdicts (and identical
    # effects), run twice from a cleared ledger.
    specs = [(1, -0.5, 0.0, "PRIMARY", False, False),
             (2, -0.4, 0.0, "PRIMARY", False, False)]
    verdicts = {1: "ARM_GRAB", 2: "CONTACT"}
    runs = []
    for i in range(2):
        bc = stub_bc(vx=6.1, vy=-1.3)
        d = run_authority(stub_world(frame=4000 + i), ScriptedOPE(verdicts),
                          _build_bodies(specs), bc)
        runs.append((d.outcome, d.reason, d.winner_id, d.in_radius_count,
                     d.role_holder_count, d.arm_grab_damping,
                     d.glancing_displacement, bc.vx, bc.vy))
    check(8, "identical inputs produce identical verdicts", runs[0] == runs[1],
          "%s vs %s" % (runs[0][:3], runs[1][:3]))

    # THE PUBLISHED SURFACE round-trips through reset.
    world = stub_world(frame=5000)
    bc = stub_bc()
    d = run_authority(world, ScriptedOPE({1: "CONTACT"}),
                      [stub_body(1, bc.x - 0.4, bc.y)], bc)
    check(8, "the decision surface is populated before the reset",
          ca.last_decision(world) is d
          and getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0) > 0
          and ca.last_contest(world) is d)
    ca.reset_contact_authority(world)
    check(8, "reset clears the published decision surface",
          ca.last_decision(world) is None
          and ca.last_contest(world) is None
          and not hasattr(world, ca.CONTACT_AUTHORITY_FRAME_ATTR))

    # The surface names are DECLARED constants, referenced by name.
    check(8, "the surface attribute names are declared, not invented inline",
          ca.CONTACT_AUTHORITY_DECISION_ATTR == "contact_authority_decision"
          and ca.CONTACT_AUTHORITY_SEQ_ATTR == "contact_authority_seq"
          and ca.CONTACT_AUTHORITY_CONTEST_ATTR
          == "contact_authority_contest"
          and ca.CONTACT_AUTHORITY_FRAME_ATTR == "_contact_authority_frame"
          and "CONTACT_AUTHORITY_DECISION_ATTR" in code_names(src)
          and "CONTACT_AUTHORITY_CONTEST_ATTR" in code_names(src))


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section, write the JSON report, return the exit code."""
    print("=" * 74)
    print("CONTACT AUTHORITY CONTRACT VERIFICATION (Stage 1B)")
    print("(one declared owner of contest formation; no behaviour change)")
    print("=" * 74)
    print("contact radius (from world/open_play_engine) = %.4f m" % RADIUS)

    verify_surface()
    verify_conjuncts()
    verify_equivalence()
    verify_contact_space_miss_clause()
    verify_evidence_providers()
    verify_tes_entry_path()
    verify_no_behaviour_change()
    verify_audit()
    verify_import_safety_and_state()

    failures = [r for r in RESULTS if not r["ok"]]
    passed = len(RESULTS) - len(failures)

    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "stage": "1B",
            "radius_m": RADIUS,
            "passed": passed,
            "failed": len(failures),
            "results": RESULTS,
        }, fh, indent=2, sort_keys=True)

    print("\nReport written to %s" % REPORT_PATH)
    print("\n" + "=" * 74)
    if failures:
        for r in failures:
            print("  FAILED: [%s] %s - %s"
                  % (r["section"], r["name"], r["detail"]))
        print("RESULT: %d / %d CHECKS FAILED"
              % (len(failures), len(RESULTS)))
    else:
        print("RESULT: ALL %d CHECKS PASSED" % len(RESULTS))
    print("=" * 74)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
