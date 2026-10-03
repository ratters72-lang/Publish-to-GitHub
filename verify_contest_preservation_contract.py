"""verify_contest_preservation_contract.py — STAGE 2A CONTRACT VERIFICATION.

The static gate for Stage 2A — Contest Preservation.  It verifies the
DECLARATION (what the preservation layer IS), not a rollout.

WHAT IT PROVES, AND WHY IT IS NOT A GUARD
-----------------------------------------
``tmp_run_stage2a_regression.py`` measures the LIVE engine.  This file proves
the properties that hold REGARDLESS of any rollout, in particular the one
Stage 2A's whole design rests on:

    ONLY ITS DECLARED READERS MAY READ THE RECORD.

That is what makes preservation behaviour-neutral, and it is stronger than any
measured comparison, because it does not depend on the simulation being
deterministic — and it is NOT: three seeded boots were measured to start from
an identical world and end in different ones, so two runs of the same code
diverge.  A cross-run trajectory diff therefore cannot separate "the recorder
changed the engine" from "the engine is chaotic".  The unread-output scan in
section [5] can, and does: it is absolute.  Since the multi-body consumption
stage the scan asserts a CLOSED ALLOW-LIST of readers rather than an empty
set: exactly one module is declared, and an undeclared reader is still the
defect Stage 2A named.

SECTIONS
--------
  [1] THE SURFACE ......... the three modules, the two published attributes,
                            the record's declared fields, the opt-out switch.
  [2] THE VOCABULARY ...... the closed disposition set, its declared subsets,
                            and the fact that the DECIDER's vocabulary did NOT
                            grow (Stage 1B's contract is untouched).
  [3] THE LEGALITY ........ L1-L5 from ``contest_record``, exercised against
                            the REAL builder with a REAL multi-body line, on
                            BOTH declaration paths.
  [4] THE DECLARATION ..... both declaration points record; the reach is
                            function-local; all five call sites pass the frame
                            population; the authority's API is unchanged.
  [5] NEUTRALITY .......... (a) the recorder stores nothing but its own two
                            world attributes, (b) its code names no decider,
                            (c) ONLY ITS DECLARED READERS READ THE RECORD, and
                            they reach it through the accessor API.
  [6] THE AUDIT SINK ...... off by default, column-keyed, file-only, and its
                            declared columns cannot drift from the record.
  [7] THE RECORD'S SHAPE .. the record carries all EIGHT things Stage 2A
                            promises at the boundary: winner, evaluated bodies,
                            in-radius candidates, formation evidence, admission
                            channel, frame, sequence, decision outcome.

Run:  python verify_contest_preservation_contract.py
Writes: verify_contest_preservation_contract_report.json
"""

from __future__ import annotations

import ast
import json
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

REPORT_PATH = "verify_contest_preservation_contract_report.json"

RECORD_MODULE = "game/defence/defence_lvl_1/contest_record.py"
PRESERVATION_MODULE = "game/defence/defence_lvl_1/contest_preservation.py"
AUDIT_MODULE = "game/defence/defence_lvl_1/contest_preservation_audit.py"
AUTHORITY_MODULE = "game/defence/defence_lvl_1/contact_authority.py"
MANAGER_MODULE = "game/managers/defence_manager.py"

# The modules that are ALLOWED to name the recorder: the recorder itself, its
# pure-data leaf, and the single authority that writes through it.  The engine
# may DECLARE a contest; only the authority may RECORD one.
_WRITERS = {PRESERVATION_MODULE, AUTHORITY_MODULE}

# Engine trees that must not read the record.  ``debug_archive`` is excluded:
# it holds historical copies that are not importable engine code.
_SCAN_ROOTS = ("game", "world", "player", "ball")
_SKIP_DIRS = ("debug_archive", "__pycache__", ".git")

RESULTS = []


def check(section: str, name: str, ok, detail=""):
    """Record one check result."""
    ok = bool(ok)
    RESULTS.append({
        "section": section, "name": name,
        "passed": ok, "detail": str(detail),
    })
    print("  %s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def info(label: str, detail):
    """Print one informational measurement."""
    print("  INFO  %-46s %s" % (label, detail))


def read_source(path: str) -> str:
    """The full source of *path*."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def doc_statement_ids(tree) -> set:
    """``id()`` of every docstring constant node (module/class/function)."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def code_names(src: str) -> set:
    """Every identifier NAME referenced by EXECUTABLE code (docstrings out).

    Identifier-shaped non-docstring string literals count too, because the
    ``getattr(world, "contest_record")`` idiom hides a name in a string.
    """
    tree = ast.parse(src)
    doc_ids = doc_statement_ids(tree)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in doc_ids and node.value.isidentifier():
                names.add(node.value)
    return names


def attribute_stores(src: str) -> list:
    """Every attribute / subscript STORE target in *src*, as source text.

    Catches ``body.x = ...``, ``body.x += ...``, ``world.a[0] = ...`` and
    ``setattr(...)`` call sites (returned as ``setattr``).
    """
    tree = ast.parse(src)
    stores = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute):
                stores.append("attribute")
            elif isinstance(target, ast.Subscript):
                stores.append("subscript")
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"):
            stores.append("setattr")
    return stores


def iter_engine_modules():
    """Yield every ``.py`` path under the scanned engine trees."""
    for root in _SCAN_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name).replace("\\", "/")


# ─────────────────────────────────────────────────────────────────────
# [1] THE SURFACE
# ─────────────────────────────────────────────────────────────────────

def section_surface():
    """The three modules, the published attribute pair, the opt-out switch."""
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_preservation_audit as cpa
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[1] THE SURFACE - one record type, one recorder, one published pair")
    check("1", "contest_record.py imports", True, "importable")
    check("1", "contest_preservation.py imports", True, "importable")
    check("1", "contest_preservation_audit.py imports", True, "importable")

    record_fields = tuple(cr.ContestRecord.__dataclass_fields__)
    for promised in ("winner_id", "candidates", "channel", "frame", "seq",
                     "outcome", "reason", "admission_predicate"):
        check("1", "the record declares '%s'" % promised,
              promised in record_fields)

    check("1", "the published attribute pair is exactly two names",
          cp.CONTEST_RECORD_ATTR == "contest_record"
          and cp.CONTEST_RECORD_INDEX_ATTR == "contest_record_index",
          "%s / %s" % (cp.CONTEST_RECORD_ATTR,
                       cp.CONTEST_RECORD_INDEX_ATTR))

    previous = os.environ.pop(cp.PRESERVATION_ENV_VAR, None)
    on_by_default = cp.preservation_enabled()
    os.environ[cp.PRESERVATION_ENV_VAR] = "0"
    off_when_zero = cp.preservation_enabled()
    os.environ[cp.PRESERVATION_ENV_VAR] = "1"
    on_when_one = cp.preservation_enabled()
    if previous is None:
        os.environ.pop(cp.PRESERVATION_ENV_VAR, None)
    else:
        os.environ[cp.PRESERVATION_ENV_VAR] = previous

    check("1", "preservation is ON by default (the switch is opt-OUT)",
          on_by_default and on_when_one and not off_when_zero,
          "default=%s zero=%s one=%s"
          % (on_by_default, off_when_zero, on_when_one))

    check("1", "the audit sink is OFF by default",
          not cpa.audit_enabled(),
          "RLM_CONTEST_PRESERVATION_AUDIT=%s"
          % os.environ.get(cpa.AUDIT_ENV_VAR, "<unset>"))

    project_imports = [
        node.module for node in ast.walk(ast.parse(read_source(RECORD_MODULE)))
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".")[0]
        not in ("dataclasses", "enum", "__future__")
    ]
    check("1", "contest_record is a PURE LEAF (no project imports)",
          not project_imports, "project imports=%s" % project_imports)


# ─────────────────────────────────────────────────────────────────────
# [2] THE VOCABULARY
# ─────────────────────────────────────────────────────────────────────

def section_vocabulary():
    """The closed disposition set, its subsets, and the DECIDER's untouched."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[2] THE VOCABULARY - six dispositions, the decider's untouched")
    members = list(cr.CandidateDisposition)
    values = sorted(member.value for member in members)
    check("2", "the disposition vocabulary is CLOSED and has six members",
          len(members) == 6, "got %d" % len(members))
    check("2", "the disposition values are distinct",
          len(set(values)) == 6, str(values))
    check("2", "the members are exactly the declared six",
          values == ["admitted_by_channel_predicate", "evaluated_rejected",
                     "evaluated_winner", "in_radius_not_evaluated",
                     "not_a_candidate", "role_holder_out_of_radius"])

    check("2", "EVALUATED_DISPOSITIONS is exactly the three deciding outcomes",
          cr.EVALUATED_DISPOSITIONS == frozenset({
              cr.CandidateDisposition.EVALUATED_WINNER,
              cr.CandidateDisposition.EVALUATED_REJECTED,
              cr.CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE}))
    check("2", "WINNER_DISPOSITIONS is exactly the two admitting outcomes",
          cr.WINNER_DISPOSITIONS == frozenset({
              cr.CandidateDisposition.EVALUATED_WINNER,
              cr.CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE}))
    check("2", "IN_RADIUS_ONLY_DISPOSITION is the never-evaluated row",
          cr.IN_RADIUS_ONLY_DISPOSITION
          is cr.CandidateDisposition.IN_RADIUS_NOT_EVALUATED)
    check("2", "OUT_OF_RADIUS_DISPOSITION is the out-of-radius row",
          cr.OUT_OF_RADIUS_DISPOSITION
          is cr.CandidateDisposition.ROLE_HOLDER_OUT_OF_RADIUS)
    check("2", "every disposition is REACHABLE in the recorder's CODE",
          _every_disposition_reachable(),
          "each member is named by a branch in contest_preservation.py")

    # Stage 1B's vocabulary must NOT have grown: its differential-equivalence
    # matrix and its reason-coverage probe are built on this exact set.
    reasons = sorted(r.value for r in ca.ContactFailureReason)
    check("2", "the DECIDER's reason vocabulary did NOT grow",
          len(reasons) == 8, "got %d: %s" % (len(reasons), reasons))
    check("2", "the decider still declares exactly two outcomes",
          len(list(ca.ContactOutcome)) == 2)


def _every_disposition_reachable() -> bool:
    """True when the recorder names every disposition member in EXECUTABLE code.

    A member appearing only in a docstring would be a dead branch; the AST scan
    ignores docstrings precisely so it can tell the two apart.
    """
    from game.defence.defence_lvl_1 import contest_record as cr
    names = code_names(read_source(PRESERVATION_MODULE))
    return all(member.name in names for member in cr.CandidateDisposition)


# ─────────────────────────────────────────────────────────────────────
# A REAL LINE TO TEST THE LAWS ON
# ─────────────────────────────────────────────────────────────────────

class _StubOPE:
    """A minimal OPE: scripted verdicts, and it records what it was asked."""

    def __init__(self, verdicts):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts)
        self.calls = []
        self.start_calls = []

    def _check_pre_tackle(self, bc, body):
        self.calls.append(getattr(body, "player_id", None))
        return self.verdicts.get(getattr(body, "player_id", None), "MISS")

    def _start_tackle(self, bc, body):
        self.start_calls.append(getattr(body, "player_id", None))


def _make_body(pid, x, y, role="PRIMARY"):
    """A REAL ``Player`` carrying the published stamps the authority reads."""
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


def _make_bc():
    """A REAL carrier two metres from the line of defenders."""
    from player.player import Player
    bc = Player(x=60.0, y=35.0, player_id=1)
    bc.vx = 5.0
    bc.vy = 0.0
    bc.tackle_role = None
    bc.fullback_role_active = False
    return bc


def _make_world(frame, team):
    """A minimal world surface (the layer reads only these attributes)."""
    from types import SimpleNamespace
    return SimpleNamespace(
        frame_count=int(frame), ball_carrier=None, defending_team=list(team),
        tes_active=False, ball_owner="BC", players=[],
    )


# ─────────────────────────────────────────────────────────────────────
# [3] THE LEGALITY
# ─────────────────────────────────────────────────────────────────────

def section_legality():
    """L1-L5, exercised against the REAL builder on BOTH declaration paths."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_record as cr
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[3] THE LEGALITY - the five laws, on a real multi-body line")

    bc = _make_bc()
    missed = _make_body(10, 59.6, 35.0)              # in radius, scores MISS
    won = _make_body(11, 59.7, 35.0)                 # in radius, scores CONTACT
    late = _make_body(12, 59.8, 35.0)                # in radius, NEVER reached
    roleless = _make_body(13, 59.9, 35.0, role=None)  # in radius, no tackle role
    distant = _make_body(14, 55.0, 35.0)             # role holder, OUT of radius
    line = [missed, won, late, roleless, distant]

    world = _make_world(21, line)
    ope = _StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    record = cp.last_record(world)

    check("3", "the decider formed on the second body",
          decision.outcome is ca.ContactOutcome.CONTACT_FORMED
          and decision.winner_id == 11,
          "outcome=%s winner=%s" % (decision.outcome.value,
                                    decision.winner_id))
    check("3", "the decider evaluated exactly two bodies, in caller order",
          ope.calls == [10, 11], "predicate calls=%s" % ope.calls)
    check("3", "ONE complete record was published", record is not None)

    if record is None:
        return

    # ── L1: the closed vocabulary ──
    check("3", "L1 every row carries a closed-vocabulary disposition",
          all(isinstance(row.disposition, cr.CandidateDisposition)
              for row in record.candidates),
          "rows=%d" % len(record.candidates))

    # ── L2: the winner ──
    winner_row = record.winner
    check("3", "L2 the winner row exists and is a WINNER disposition",
          winner_row is not None
          and winner_row.disposition in cr.WINNER_DISPOSITIONS,
          "disposition=%s"
          % (None if winner_row is None else winner_row.disposition.value))
    check("3", "L2 the decider's winner is in BOTH derived views",
          record.winner_id in record.evaluated_ids
          and record.winner_id in record.in_radius_ids,
          "evaluated=%s in_radius=%s"
          % (record.evaluated_ids, record.in_radius_ids))

    # ── L3: the views agree with the counts ──
    check("3", "L3 every view agrees with its own count",
          record.candidate_count == len(record.candidates)
          and record.evaluated_count == len(record.evaluated)
          and record.in_radius_count == len(record.in_radius),
          "candidates=%d evaluated=%d in_radius=%d"
          % (record.candidate_count, record.evaluated_count,
             record.in_radius_count))

    # ── L4: geometry is geometry ──
    late_row = record.row_for(12)
    roleless_row = record.row_for(13)
    distant_row = record.row_for(14)
    check("3", "L4 IN_RADIUS_NOT_EVALUATED implies in_radius",
          late_row is not None and late_row.in_radius
          and late_row.disposition is cr.IN_RADIUS_ONLY_DISPOSITION,
          "row 12: in_radius=%s disposition=%s"
          % (None if late_row is None else late_row.in_radius,
             None if late_row is None else late_row.disposition.value))
    check("3", "L4 ROLE_HOLDER_OUT_OF_RADIUS implies NOT in_radius",
          distant_row is not None and not distant_row.in_radius
          and distant_row.disposition is cr.OUT_OF_RADIUS_DISPOSITION,
          "row 14: in_radius=%s disposition=%s"
          % (None if distant_row is None else distant_row.in_radius,
             None if distant_row is None else distant_row.disposition.value))
    check("3", "L4 an IN-RADIUS roleless body is NOT_A_CANDIDATE",
          roleless_row is not None and roleless_row.in_radius
          and roleless_row.disposition is cr.CandidateDisposition.NOT_A_CANDIDATE,
          "row 13: in_radius=%s disposition=%s"
          % (None if roleless_row is None else roleless_row.in_radius,
             None if roleless_row is None else roleless_row.disposition.value))

    # ── L5: THE ANTI-COMPRESSION PROPERTY ──
    check("3", "L5 the record HOLDS the body Stage 1B used to discard",
          record.un_evaluated_in_radius_ids == (12,),
          "discarded=%s" % (record.un_evaluated_in_radius_ids,))
    check("3", "L5 and it is exactly the never-evaluated rows",
          all(row.disposition is cr.IN_RADIUS_ONLY_DISPOSITION
              for row in record.un_evaluated_in_radius)
          and record.evaluated_ids == (10, 11),
          "evaluated=%s in_radius=%s"
          % (record.evaluated_ids, record.in_radius_ids))

    # ── the ENGAGE path, and L2's channel split ──
    far_winner = _make_body(20, 63.0, 35.0)   # 3 m: outside the 1.5 m radius
    world2 = _make_world(22, line)
    ope2 = _StubOPE({})
    ca.reset_contact_authority(world2)
    cp.reset_preservation(world2)
    ca.enter_tes(world2, ope2, bc, far_winner,
                 ca.CHANNEL_STRUCTURED_ENGAGE, defenders=[far_winner] + line)
    record2 = cp.last_record(world2)

    check("3", "the engage channel entered TES on the admitted body",
          ope2.start_calls == [20], "start_calls=%s" % ope2.start_calls)
    check("3", "the engage admission published a record", record2 is not None)
    if record2 is None:
        return

    check("3", "the engage record names its channel and its decider",
          record2.channel == ca.CHANNEL_STRUCTURED_ENGAGE
          and record2.decider == cp.DECIDER_CHANNEL,
          "%s / %s" % (record2.channel, record2.decider))
    check("3", "the engage record names the predicate that admitted it",
          record2.admission_predicate
          == "game.defence.defence_lvl_1.pretackle_machine.should_engage"
          and record2.admission_site
          == ("game.defence.defence_lvl_1.defensive_behaviour_unified"
              "._apply_structured_open_play"),
          "%s @ %s" % (record2.admission_predicate,
                       record2.admission_site))
    check("3", "L2 a CHANNEL winner may sit OUTSIDE the decider's radius",
          record2.winner_id == 20
          and record2.winner is not None
          and not record2.winner.in_radius
          and record2.winner.disposition
          is cr.CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE,
          "winner=%s in_radius=%s"
          % (record2.winner_id,
             None if record2.winner is None else record2.winner.in_radius))

    # A channel admission with no winner declares nothing.
    world3 = _make_world(23, line)
    cp.reset_preservation(world3)
    nothing = cp.preserve_channel_admission(world3, bc, None,
                                           ca.CHANNEL_STRUCTURED_ENGAGE,
                                           defenders=line)
    check("3", "a channel admission with NO winner records nothing",
          nothing is None and cp.last_record(world3) is None,
          "returned=%s published=%s"
          % (nothing, cp.last_record(world3)))


# ─────────────────────────────────────────────────────────────────────
# [4] THE DECLARATION
# ─────────────────────────────────────────────────────────────────────

def section_declaration():
    """Both declaration points record; five call sites pass the population."""
    from game.defence.defence_lvl_1 import contest_preservation as cp

    print("\n[4] THE DECLARATION - both points, five call sites, local reach")
    authority = read_source(AUTHORITY_MODULE)
    manager = read_source(MANAGER_MODULE)
    unified = read_source("game/defence/defence_lvl_1/"
                          "defensive_behaviour_unified.py")
    lvl1 = read_source("game/defence/defence_lvl_1/"
                       "defensive_behaviour_lvl1.py")

    # The authority's own calls are read from the AST, not from a source
    # substring: the formatter may wrap a call across lines, and a substring
    # test would then report a real call as missing.
    authority_tree = ast.parse(authority)
    calls_preserve = []
    calls_channel = []
    handover_lines = []
    for node in ast.walk(authority_tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            if node.func.id == "_preserve":
                calls_preserve.append(node.lineno)
            elif node.func.id == "_preserve_channel":
                calls_channel.append(node.lineno)
        elif (isinstance(node.func, ast.Attribute)
              and node.func.attr == "_start_tackle"):
            handover_lines.append(node.lineno)

    check("4", "the decider's decision is preserved at BOTH returns",
          len(calls_preserve) == 2,
          "call sites at lines %s" % calls_preserve)
    check("4", "the channel admission is preserved exactly ONCE",
          len(calls_channel) == 1,
          "call site at line %s" % calls_channel)
    check("4", "the authority still hands over at exactly TWO points",
          len(handover_lines) == 2,
          "handover lines=%s (the engage channel, then the manager)"
          % handover_lines)
    check("4", "and the channel preservation sits at a handover",
          len(calls_channel) == 1
          and calls_channel[0] in [line - 1 for line in handover_lines],
          "preserve at %s, handovers at %s"
          % (calls_channel, handover_lines))

    enter_def = next((node for node in ast.walk(authority_tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "enter_tes"), None)
    enter_params = ([arg.arg for arg in enter_def.args.args]
                    if enter_def is not None else [])
    check("4", "enter_tes accepts the frame population",
          "defenders" in enter_params,
          "enter_tes parameters=%s" % enter_params)
    check("4", "the authority passes it POSITIONALLY, not by keyword",
          "defenders=defenders" not in authority,
          "so the five CALLERS are the only keyword sites")

    counts = {
        "defensive_behaviour_unified.py": unified.count("defenders=defenders"),
        "defensive_behaviour_lvl1.py": lvl1.count("defenders=defenders"),
        "defence_manager.py": manager.count("defenders=defenders"),
    }
    check("4", "all FIVE declaration sites pass the frame population",
          sum(counts.values()) == 5,
          "per file=%s" % counts)

    check("4", "the manager still hands over through the TES choke point",
          "enter_tes(world, ope, bc, _contact_decision.winner" in manager
          and "already_decided=True" in manager,
          "the verified Stage 1B call string is intact")

    # The reach must be FUNCTION-LOCAL: a module-level import of the recorder
    # would join the authority to Stage 2A's import graph.
    tree = ast.parse(authority)
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            top_level.add(node.module or "")
        elif isinstance(node, ast.Import):
            top_level.update(alias.name for alias in node.names)
    check("4", "the recorder is NOT imported at the authority's module level",
          not any("contest_preservation" in name for name in top_level),
          "top-level imports=%s" % sorted(top_level))

    from game.defence.defence_lvl_1 import contact_authority as ca
    api = ("evaluate_contact", "enter_tes", "ContactOutcome",
           "ContactFailureReason", "CHANNEL_MANAGER_TRIGGER",
           "CHANNEL_STRUCTURED_ENGAGE", "CHANNEL_UNIFIED_ENGAGE",
           "CHANNEL_PURSUIT_ENGAGE", "CHANNEL_LVL1_CHASE_ENGAGE")
    missing = [name for name in api if not hasattr(ca, name)]
    check("4", "the authority's declared API is unchanged", not missing,
          "missing=%s" % missing)

    table = cp.declared_admission_channels()
    check("4", "the declared admission table covers EXACTLY the channels",
          set(table) == {ca.CHANNEL_MANAGER_TRIGGER,
                         ca.CHANNEL_STRUCTURED_ENGAGE,
                         ca.CHANNEL_UNIFIED_ENGAGE,
                         ca.CHANNEL_PURSUIT_ENGAGE,
                         ca.CHANNEL_LVL1_CHASE_ENGAGE},
          "channels=%s" % sorted(table))
    resolved = _predicates_exist(table)
    unresolved = sorted({predicate for predicate, _site in table.values()}
                        - set(resolved))
    check("4", "every declared admission predicate RESOLVES in its module",
          not unresolved,
          "resolved=%s%s" % (sorted(resolved.values()),
                             "" if not unresolved
                             else " UNRESOLVED=%s" % unresolved))
    check("4", "an UNDECLARED channel is reported, never guessed",
          cp.admission_for("no_such_channel")
          == ("UNDECLARED_CHANNEL", "UNDECLARED_CHANNEL"))


def _predicates_exist(table) -> dict:
    """Resolve every predicate named in the admission table.

    Returns ``{predicate: how it resolved}`` for the ones that RESOLVE.  A
    predicate resolves either as a module-level function OR as a METHOD of a
    class the module defines — the manager channel's predicate is
    ``OpenPlayEngine._check_pre_tackle``, which the authority calls as
    ``ope._check_pre_tackle(bc, body)`` on the engine INSTANCE.  A plain module
    ``hasattr`` would therefore call a correct declaration a lie.
    (Measured 2026-09-20: it did, and this is the corrected form.)
    """
    import importlib
    resolved = {}
    for _channel, (predicate, _site) in table.items():
        module_path, _, attribute = predicate.rpartition(".")
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue
        if hasattr(module, attribute):
            resolved[predicate] = "module function"
            continue
        for name in dir(module):
            candidate = getattr(module, name)
            if isinstance(candidate, type) and hasattr(candidate, attribute):
                resolved[predicate] = "%s method" % name
                break
    return resolved
# ─────────────────────────────────────────────────────────────────────
# [5] NEUTRALITY — THE ABSOLUTE PROOF
# ─────────────────────────────────────────────────────────────────────

# Everything an engine module would have to mention in order to CONSUME the
# record: the reader API and the two published attribute names.
_READER_NAMES = {
    "last_record", "recent_records", "record_for", "preserve_decider_decision",
    "preserve_channel_admission", "reset_preservation",
    "format_preservation_report", "declared_admission_channels",
    "admission_for", "preservation_enabled",
}
_ATTR_NAMES = {"contest_record", "contest_record_index"}

# THE DECLARED READERS.  Stage 2A's law was that NOTHING read the record: the
# recorder was pure observation, and any consumer would have been a defect.
# The multi-body consumption stage makes exactly ONE deliberate reader real --
# the TES entry, which derives a tackle's collaborative wrap model from the
# declaration Contact Authority signed.  The law is therefore a CLOSED
# ALLOW-LIST rather than an empty set: a reader NOT named here is still a
# defect, and a reader named here that reads something else is a lie.
_RECORD_READERS = {
    "game/tes/tes.py": ("last_record", "record_for"),
}

# A control: a module that READS the record must be caught, so the scan cannot
# pass merely because nothing was ever inspected.
_CONTROL_READER = (
    "def peek(world):\n"
    "    return getattr(world, \"contest_record\", None)\n"
)
_CONTROL_DOCSTRING = (
    "\"\"\"A module that merely TALKS about world.contest_record.\"\"\"\n"
    "def peek(world):\n"
    "    return None\n"
)


def reader_hits(src: str) -> list:
    """Every recorder name/attribute *src* touches in EXECUTABLE code."""
    return sorted((_READER_NAMES | _ATTR_NAMES) & code_names(src))


def section_neutrality():
    """Why preservation cannot change a frame — proven, not asserted."""
    from game.defence.defence_lvl_1 import contest_preservation as cp

    print("\n[5] NEUTRALITY - nothing writes it, only its DECLARED readers read")
    source = read_source(PRESERVATION_MODULE)

    # ── 5a: the recorder stores nothing but its own two world attributes ──
    stores = attribute_stores(source)
    check("5", "the recorder performs NO attribute or subscript store",
          stores.count("attribute") == 0 and stores.count("subscript") == 0,
          "attribute=%d subscript=%d"
          % (stores.count("attribute"), stores.count("subscript")))
    check("5", "its only store is exactly TWO setattr calls",
          stores.count("setattr") == 2,
          "setattr=%d" % stores.count("setattr"))

    tree = ast.parse(source)
    targets = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2):
            holder = node.args[0]
            target = node.args[1]
            targets.append((
                getattr(holder, "id", "?"),
                getattr(target, "id", "?"),
            ))
    check("5", "both setattr calls target the WORLD and its declared names",
          targets == [("world", "CONTEST_RECORD_ATTR"),
                      ("world", "CONTEST_RECORD_INDEX_ATTR")],
          "targets=%s" % (targets,))
    check("5", "the published names are the declared constant pair",
          cp.CONTEST_RECORD_ATTR == "contest_record"
          and cp.CONTEST_RECORD_INDEX_ATTR == "contest_record_index")

    # ── 5b: the recorder consults no decider and enters no TES ──
    names = code_names(source)
    forbidden = {"evaluate_contact", "enter_tes", "should_engage",
                 "run_machine", "_check_pre_tackle", "_start_tackle",
                 "tes_start_tackle"}
    check("5", "the recorder's CODE names no decider and no TES entry",
          not (forbidden & names),
          "touched=%s" % sorted(forbidden & names))
    check("5", "the audit sink is optional in the recorder's own code",
          "if audit_enabled():" in source,
          "the CSV write is guarded, so deleting it changes nothing")

    # ── 5c: THE REPO-WIDE UNREAD SCAN ──
    readers = {}
    scanned_paths = []
    skipped = 0
    for path in iter_engine_modules():
        if path in _WRITERS:
            continue
        try:
            src = read_source(path)
        except OSError:
            skipped += 1
            continue
        try:
            hits = reader_hits(src)
        except SyntaxError:
            skipped += 1
            continue
        scanned_paths.append(path)
        if hits:
            readers[path] = hits

    scanned = len(scanned_paths)

    # THE ALLOW-LIST LAW, in independently failable parts:
    #   (a) does any module read the record WITHOUT being declared?  An
    #       accidental consumer is still exactly the defect Stage 2A named.
    #   (b) does every declared reader read EXACTLY its declared accessors?
    #       A stale entry is a lie, and reading an UNDECLARED accessor (the
    #       recorder's own switch, say) is a consumer nobody sanctioned.
    #   (c) does a declared reader reach the record through the module's
    #       ACCESSOR API, never the raw published world attribute?
    undeclared = {path: hits for path, hits in readers.items()
                  if path not in _RECORD_READERS}
    check("5", "NO UNDECLARED importable engine module reads the record",
          not undeclared, "undeclared readers=%s" % undeclared)

    mismatched = {}
    for path, declared in _RECORD_READERS.items():
        got = tuple(readers.get(path, ()))
        if set(got) != set(declared):
            mismatched[path] = (sorted(declared), sorted(got))
    check("5", "every declared reader reads EXACTLY its declared accessors",
          not mismatched, "declared vs actual=%s" % mismatched)

    raw = sorted({name for path in _RECORD_READERS
                  for name in readers.get(path, ()) if name in _ATTR_NAMES})
    check("5", "the declared readers reach the record via the ACCESSOR API",
          not raw, "raw world attributes named by a reader=%s" % raw)

    check("5", "the readers of the record are EXACTLY the declared set",
          sorted(readers) == sorted(_RECORD_READERS),
          "readers=%s declared=%s" % (sorted(readers), sorted(_RECORD_READERS)))
    # The count is the IMPORTABLE engine tree: ``debug_archive`` holds whole
    # historical copies of the project, which the walk skips on purpose, so a
    # count that included them would be measuring the archive, not the engine.
    check("5", "the scan is genuinely repo-wide",
          scanned >= 100, "scanned=%d skipped=%d" % (scanned, skipped))
    check("5", "the scan EXCLUDES the historical archive",
          not [path for path in scanned_paths if "debug_archive" in path],
          "no archived copy of the tree is counted as engine code")

    reached_manager = [path for path in scanned_paths
                       if path.endswith("game/managers/defence_manager.py")]
    check("5", "the walk really reaches the manager",
          len(reached_manager) == 1, "reached=%s" % reached_manager)
    check("5", "and the authority is SKIPPED as a declared writer",
          AUTHORITY_MODULE in _WRITERS
          and AUTHORITY_MODULE not in scanned_paths,
          "only the recorder and the authority may name the recorder")
    check("5", "the detector really fires - a READER is caught",
          reader_hits(_CONTROL_READER) == ["contest_record"],
          "control hits=%s" % reader_hits(_CONTROL_READER))
    check("5", "and a docstring MENTION is not a read",
          reader_hits(_CONTROL_DOCSTRING) == [],
          "control hits=%s" % reader_hits(_CONTROL_DOCSTRING))

    info("engine modules scanned", "%d (skipped %d)" % (scanned, skipped))


# ─────────────────────────────────────────────────────────────────────
# [6] THE AUDIT SINK
# ─────────────────────────────────────────────────────────────────────

def section_audit():
    """The optional CSV sink: off, column-keyed, file-only, drift-proof."""
    from game.defence.defence_lvl_1 import contest_preservation_audit as cpa
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[6] THE AUDIT SINK - off by default, column-keyed, file-only")
    columns = list(cpa.AUDIT_COLUMNS)
    check("6", "the declared columns are unique",
          len(columns) == len(set(columns)),
          "columns=%d" % len(columns))
    check("6", "the rows are COLUMN-KEYED (a row cannot drift)",
          "csv.DictWriter" in read_source(AUDIT_MODULE)
          and "fieldnames=list(AUDIT_COLUMNS)" in read_source(AUDIT_MODULE))

    # Every record field must be written somewhere; two fields expand.
    # ``carrier`` is a ``CarrierSnapshot`` and expands to one column per field
    # of that dataclass, ``bc_``-prefixed like the kinematics.  The trait pair
    # is listed here so this law keeps demanding a column for EVERY carrier
    # field now that Stage 3.1 added two; naming only the kinematics would let
    # the sink fall silently behind the shape it is declared to cover.
    aliases = {
        "candidates": ("body_id", "disposition", "is_winner",
                       "never_evaluated"),
        "carrier": ("bc_x", "bc_y", "bc_vx", "bc_vy",
                    "bc_fend_trait", "bc_tackle_break_trait"),
    }
    missing = []
    for field_name in cr.ContestRecord.__dataclass_fields__:
        wanted = aliases.get(field_name, (field_name,))
        missing += [name for name in wanted if name not in columns]
    check("6", "the columns cover every field of the RECORD", not missing,
          "missing=%s" % missing)

    body_missing = []
    for field_name in cr.CandidateEvidence.__dataclass_fields__:
        wanted = ("body_id",) if field_name == "player_id" else (field_name,)
        body_missing += [name for name in wanted if name not in columns]
    check("6", "the columns cover every field of a BODY ROW",
          not body_missing, "missing=%s" % body_missing)

    check("6", "the sink stores NOTHING on any object",
          attribute_stores(read_source(AUDIT_MODULE)).count("attribute") == 0
          and attribute_stores(read_source(AUDIT_MODULE)).count("setattr") == 0,
          "it writes a file and nothing else")

    check("6", "a blank row carries every declared column",
          set(cpa._blank_row()) == set(columns),
          "keys=%d" % len(cpa._blank_row()))


# ─────────────────────────────────────────────────────────────────────
# [7] THE RECORD'S SHAPE — the eight things Stage 2A promises
# ─────────────────────────────────────────────────────────────────────

def section_shape():
    """One record, and the eight facts it must carry at the boundary."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_record as cr
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    print("\n[7] THE RECORD'S SHAPE - the eight facts, on one declaration")

    bc = _make_bc()
    first = _make_body(10, 59.6, 35.0)
    second = _make_body(11, 59.7, 35.0)
    third = _make_body(12, 59.8, 35.0)
    line = [first, second, third]

    world = _make_world(31, line)
    ope = _StubOPE({10: "MISS", 11: "CONTACT"})
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    record = cp.last_record(world)

    if record is None:
        check("7", "a declaration published a record", False, "none")
        return

    check("7", "1. THE WINNER", record.winner is not None
          and record.winner_id == 11, "winner=%s" % record.winner_id)
    check("7", "2. THE EVALUATED BODIES", record.evaluated_ids == (10, 11),
          "evaluated=%s" % (record.evaluated_ids,))
    check("7", "3. THE IN-RADIUS CANDIDATES",
          record.in_radius_ids == (10, 11, 12),
          "in_radius=%s" % (record.in_radius_ids,))

    evidence_fields = tuple(cr.CandidateEvidence.__dataclass_fields__)
    winner_row = record.winner
    check("7", "4. THE FORMATION EVIDENCE",
          winner_row is not None and len(evidence_fields) >= 17
          and all(hasattr(winner_row, name) for name in evidence_fields),
          "%d evidence fields per body" % len(evidence_fields))

    check("7", "5. THE ADMISSION CHANNEL",
          record.channel == ca.CHANNEL_MANAGER_TRIGGER
          and record.admission_predicate
          and record.admission_site,
          "%s via %s" % (record.channel, record.admission_predicate))
    check("7", "6. THE FRAME", record.frame == 31,
          "frame=%s" % record.frame)
    check("7", "7. THE SEQUENCE", record.seq >= 0,
          "seq=%s (authority sequence)" % record.seq)
    check("7", "8. THE DECISION OUTCOME",
          record.outcome in {o.value for o in ca.ContactOutcome}
          and record.reason in {r.value for r in ca.ContactFailureReason},
          "%s / %s" % (record.outcome, record.reason))

    check("7", "the record is INDEXED, one per declaration",
          record.record_index == 1, "index=%s" % record.record_index)
    # The record's carrier is the carrier AS THE AUTHORITY LEFT IT, never a
    # snapshot taken before the frame ran.  Body 10's MISS is a CONTACT-SPACE
    # MISS -- 0.40 m from the carrier, inside the 1.10 m combined body radius --
    # so the CONTACT_SPACE_MISS reaction IS armed for it.  The reaction is a
    # scripted ballistic arc integrated by ``Player.update`` on the FOLLOWING
    # frames, so at declaration time the authority writes no position and no
    # velocity: the published snapshot must therefore equal the live carrier
    # EXACTLY, and the reaction itself is asserted by the event the decision
    # carries (see ``verify_contact_space_miss_contract.py`` and the record
    # schema contract's section [8]).  A "> 60.0" clause here would assert a
    # teleport the design forbids.
    check("7", "the record carries the carrier as the authority left it",
          record.carrier.player_id == 1
          and (record.carrier.x, record.carrier.y)
          == (float(bc.x), float(bc.y))
          and (record.carrier.vx, record.carrier.vy)
          == (float(bc.vx), float(bc.vy)),
          "(%s, %s) v=(%s, %s) live=(%.4f, %.4f)"
          % (record.carrier.x, record.carrier.y,
             record.carrier.vx, record.carrier.vy, bc.x, bc.vx))
    check("7", "the record's report renders in one line",
          cp.format_preservation_report(world).startswith(
              "contest_preservation=#1 frame=31"),
          cp.format_preservation_report(world))


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section, write the report, and return the exit code."""
    print("=" * 80)
    print("CONTEST PRESERVATION CONTRACT VERIFICATION (Stage 2A)")
    print("(the complete formation state, recorded at the authority boundary;")
    print(" no TES behaviour change, no winner change, no simulation change)")
    print("=" * 80)

    section_surface()
    section_vocabulary()
    section_legality()
    section_declaration()
    section_neutrality()
    section_audit()
    section_shape()

    failed = [row for row in RESULTS if not row["passed"]]
    report = {
        "failed": len(failed),
        "passed": len(RESULTS) - len(failed),
        "sections": sorted({row["section"] for row in RESULTS}),
        "results": RESULTS,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    print("\n" + "=" * 80)
    if failed:
        print("RESULT: %d FAIL(s)" % len(failed))
        for row in failed:
            print("  [%s] %s - %s" % (row["section"], row["name"],
                                      row["detail"]))
        print("=" * 80)
        return 1
    print("RESULT: the preservation contract holds - %d checks, 0 failures"
          % len(RESULTS))
    print("        the record is COMPLETE, and it is read ONLY by its declared")
    print("        readers, so preservation is inert in every other frame.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
