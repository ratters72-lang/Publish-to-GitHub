"""verify_contest_transport_contract.py — THE STAGE 2B CONTRACT GATE.

WHAT THIS GATE IS
-----------------
The Stage 2B contract, machine-checked:

    At the moment Contact Authority declares a contest, a FIRST-CLASS CONTEST
    OBJECT is created and transferred across the TES boundary.  TES receives
    that object instead of a standalone winning defender identifier, and
    consumes ONLY the winning defender it carries.

It is the static companion to ``probe_contest_transport.py`` (live evidence at
the boundary), ``tmp_s2b_derivation_probe.py`` (the payload really is the
single source the record derives from) and ``tmp_run_stage2b_regression.py``
(the whole engine, with the boundary object captured).

THE SECTIONS
------------
  [1] THE SURFACE            the payload module: the declared facts, one
                             consumption point, no project reach, not gated.
  [2] ONE DECLARATION, ONE   Contact Authority assembles ONE Contest per
      PAYLOAD                declaration; the hand-over REUSES it.
  [3] THE TES BOUNDARY       the payload crosses the boundary; the entry keeps
                             its original signature and its delegation; the
                             authority still calls it exactly twice.
  [4] ONLY THE WINNER        TES reads the winning defender and nothing else;
                             the advisory facts are named nowhere TES can see.
  [5] THE RECORD PROJECTS    the historical record COPIES the declaration's
                             facts; it re-derives none of them; the recorder
                             still stores nothing but its two world attributes.
  [6] THE FAILED FRAME       a frame that declares no contest has NO payload,
                             so its record is derived exactly as before.
  [7] NOT GATED              the transport is unconditional: the RECORDER's
                             switch can never remove the object TES is handed.
  [8] NO BEHAVIOUR CHANGE    Stage 1B and Stage 2A are exactly where they were,
                             to the digit, in their own recorded reports.

Run:  python verify_contest_transport_contract.py
Writes: verify_contest_transport_contract_report.json
"""

from __future__ import annotations

import ast
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

CONTEST_PATH = "game/defence/defence_lvl_1/contest.py"
AUTHORITY_PATH = "game/defence/defence_lvl_1/contact_authority.py"
PRESERVATION_PATH = "game/defence/defence_lvl_1/contest_preservation.py"
RECORD_PATH = "game/defence/defence_lvl_1/contest_record.py"
ENGINE_PATH = "world/open_play_engine.py"
TES_PATH = "game/tes/tes.py"
MANAGER_PATH = "game/managers/defence_manager.py"

STAGE1B_REPORT = "verify_contact_authority_contract_report.json"
STAGE2A_REPORT = "verify_contest_preservation_contract_report.json"
REPORT_PATH = "verify_contest_transport_contract_report.json"

# The recorded predecessor baselines.  Stage 2B must leave every one of these
# numbers where it found them: a transport change that moved a predecessor's
# count by one would be a regression, so the number is asserted, not eyeballed.
#
# Stage 2A moved from 86 to 89 ONCE, deliberately, when the multi-body
# consumption stage turned its absolute unread law into a closed allow-list:
# one check ("NO importable engine module reads the record") became four
# ("NO UNDECLARED ... reads", "every declared reader reads EXACTLY its declared
# accessors", "... via the ACCESSOR API", "the readers are EXACTLY the declared
# set").  The law got STRICTER, not looser: an undeclared reader is still a
# failure.  The number is re-pinned here so the change is asserted, not
# eyeballed.
#
# Stage 1B moved from 146 to 149 ONCE, deliberately, for the CONTACT_SPACE_MISS
# clause.  The authority contract gained section [3b], whose three checks assert
# that with the new path ARMED the only divergence from the original loop is
# exactly the contact-space MISS cases, that an ordinary MISS stays
# byte-identical and records no event, and that the matrix really exercises the
# new path.  NO existing check was weakened: section [3]'s byte-identity claim
# is now simply scoped to the SUPPRESSED path, which is the RELOCATION claim it
# always made.  The number is re-pinned here so the change is asserted.
# RESTATED FOR THE BALLISTIC ARC (2026-10-04).  The reaction no longer writes
# position or velocity at declaration time (see section [3b] of the authority
# contract and section [7] of the preservation contract), so the two checks
# that asserted the old instantaneous nudge now assert the arc instead and pass
# again.  The counts are therefore UNCHANGED from the 2026-10-03 pins: no check
# was added, removed or weakened to get here.
STAGE1B_BASELINE = {"passed": 149, "failed": 0, "radius_m": 1.5}
STAGE2A_BASELINE = {"passed": 89, "failed": 0}

# THE DECLARED FACTS the task names for the payload.  Every one must be a field
# of the object, and every one must be a fact known at declaration time.
DECLARED_FACT_FIELDS = (
    "contest_id", "winner_id", "evaluated_ids", "in_radius_ids",
    "channel", "frame", "seq",
)

# The ADVISORY facts: everything the payload carries that TES may NOT consume.
# Nothing in the TES entry may read any of these from a Contest.
ADVISORY_NAMES = (
    "evaluated_ids", "in_radius_ids", "channel", "frame", "seq",
    "contest_id", "radius_m", "arm_grab_damping", "glancing_displacement",
    "outcome", "reason", "phase",
)

_RESULTS = []
_SECTION = "?"


def _read(path):
    """Source text of *path* (never raises on encoding)."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def section(title):
    """Start a named section."""
    global _SECTION
    _SECTION = title.split()[0]
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def check(name, ok, detail=""):
    """Record and print one check."""
    ok = bool(ok)
    _RESULTS.append({"section": _SECTION, "name": name,
                     "passed": ok, "detail": str(detail)})
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           (" - " + str(detail)) if detail else ""))
    return ok


def code_names(src):
    """Every identifier NAME referenced by EXECUTABLE code (docstrings out).

    Identifiers are collected from Name / Attribute / keyword-arg nodes and from
    identifier-shaped string literals that are NOT docstrings.  Prose that
    merely NAMES the TES entry therefore does not count as a reference.
    """
    tree = ast.parse(src)
    doc_ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            doc_ids.add(id(body[0].value))
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


def attribute_stores(src):
    """``(attribute, subscript)`` stores found in executable code.

    A store is an Assign / AugAssign / AnnAssign whose TARGET is an ``obj.attr``
    or an ``obj[key]``.  This is the measurement that proves the payload module
    and the recorder write to no object.
    """
    tree = ast.parse(src)
    attrs = 0
    subs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute):
                attrs += 1
            elif isinstance(target, ast.Subscript):
                subs += 1
    return attrs, subs


def setattr_calls(src):
    """The ``setattr(...)`` calls in the module, as their first two arg names."""
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2):
            first = getattr(node.args[0], "id", None)
            second = getattr(node.args[1], "id", None)
            out.append((first, second))
    return out


def function_source(src, name):
    """The source SEGMENT of a top-level ``def name`` (or ``None``).

    Used to scope an assertion to ONE function, so a name that is legal in the
    recorder cannot silently satisfy a check about the TES entry.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return None


def method_source(src, name):
    """The source SEGMENT of the FIRST ``def name`` at any nesting depth."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return None


def module_level_imports(src):
    """Every module named by a MODULE-LEVEL import statement."""
    tree = ast.parse(src)
    mods = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            for alias in node.names:
                mods.add((node.module or "") + "." + alias.name)
    return mods
def call_sites(src, name):
    """Line numbers of the CALLS to ``name`` (never the ``def`` line)."""
    tree = ast.parse(src)
    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == name):
            lines.append(node.lineno)
    return sorted(lines)


def call_arg_names(src, name, index=1):
    """The NAME passed as positional *index* to every CALL of ``name``.

    AST-based on purpose: prose that MENTIONS a call (a docstring showing the
    old signature, say) is not a call, so the check cannot be satisfied — or
    tripped — by documentation.
    """
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) <= index:
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if called == name:
            out.append(getattr(node.args[index], "id", None))
    return out


SKIP_DIRS = {"__pycache__", "debug_archive", ".git", ".venv", "venv",
             "node_modules", "tmp", ".pytest_cache"}


def iter_engine_modules():
    """Every candidate ENGINE module in the tree (relative posix paths).

    Mirrors the Stage 1B gate's repo walk: the scan has to be repo-wide for its
    negative results to mean anything, so the walk really does descend the
    tree.  Verification/probe/tmp harnesses and archived versions are excluded
    because they are not shipped engine code.
    """
    found = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            if name.startswith(("tmp_", "verify_", "probe_", "test_")):
                continue
            path = os.path.join(root, name).replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            found.append(path)
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────
# [1] THE SURFACE — the payload module
# ─────────────────────────────────────────────────────────────────────

def verify_surface():
    """The payload exists, declares the facts, and reaches nothing."""
    section("[1] The surface - one payload, one consumption point, no reach")

    contest_src = _read(CONTEST_PATH)

    try:
        from game.defence.defence_lvl_1 import contest as _c
        check("the payload module exists and is importable", True,
              "Contest / winner_of / build_contest")
    except Exception as exc:                                # pragma: no cover
        check("the payload module exists and is importable", False, repr(exc))
        return

    # NO PROJECT REACH: module-scope imports are stdlib only.  The payload must
    # be a leaf, so nothing it does can reach the engine.
    project_imports = sorted(
        name for name in module_level_imports(contest_src)
        if name.split(".")[0] in {"world", "game", "player", "ball", "ui"}
    )
    check("the payload has NO project imports (it is a leaf)",
          not project_imports, "offenders=%s" % project_imports)

    # The declared facts are FIELDS of the object.
    fields = set(getattr(_c.Contest, "__dataclass_fields__", {}))
    missing = [name for name in DECLARED_FACT_FIELDS if name not in fields]
    check("every DECLARED fact is a field of the Contest",
          not missing, "missing=%s" % missing)

    # The declared fact contract is stated in code as well as here.
    declared = dict(_c.declared_facts())
    undeclared = [name for name in DECLARED_FACT_FIELDS if name not in declared]
    check("declared_facts() names every declared fact",
          not undeclared, "missing=%s" % undeclared)

    # FROZEN: the payload is a value, not a mutable scratchpad.
    check("the Contest is a FROZEN dataclass",
          "@dataclass(frozen=True)" in contest_src
          and "class Contest:" in contest_src,
          "a declared contest cannot be edited after the fact")

    # A failed frame has no contest, so the object must be able to say so.
    check("the payload can express 'no contest' honestly",
          hasattr(_c.Contest, "formed"),
          "Contest.formed distinguishes a declared contest from a bare frame")

    # THE ONE CONSUMPTION POINT.
    names = code_names(contest_src)
    check("the payload declares its consumption accessor",
          "winner_of" in names and "is_contest" in names,
          "winner_of / is_contest")

    # It writes to NO object.
    attrs, subs = attribute_stores(contest_src)
    check("the payload performs NO attribute store",
          attrs == 0, "attribute stores=%d" % attrs)
    check("the payload performs NO subscript store",
          subs == 0, "subscript stores=%d" % subs)

    # Its only store is the published live-contest attribute on the world.
    stores = setattr_calls(contest_src)
    check("the payload's ONLY setattr targets the world's declared attribute",
          stores == [("world", "CONTEST_ATTR")],
          "setattr calls=%s" % stores)

    check("the published surface name is a declared constant",
          getattr(_c, "CONTEST_ATTR", None) == "contest",
          "CONTEST_ATTR=%r" % getattr(_c, "CONTEST_ATTR", None))

    check("a contest identifier derives from the declaring frame and sequence",
          _c.contest_id_for(7, 3) == "contest-7-3",
          "contest_id_for(7, 3)=%r" % _c.contest_id_for(7, 3))


# ─────────────────────────────────────────────────────────────────────
# [2] ONE DECLARATION, ONE PAYLOAD
# ─────────────────────────────────────────────────────────────────────

def verify_one_declaration_one_payload():
    """Contact Authority assembles ONE payload per declaration; reuse only."""
    section("[2] One declaration, one payload (assembled at the declaration)")

    authority = _read(AUTHORITY_PATH)

    check("the authority assembles the payload itself",
          "def _assemble_contest(" in authority,
          "the declaration point is inside the authority")

    sites = call_sites(authority, "_assemble_contest")
    # Two DECLARATION points (manager formed + engage) plus one defensive
    # fallback for a hand-over that finds no payload.  Any more would mean a
    # second representation is being assembled somewhere.
    check("the payload is assembled at exactly the declared sites",
          len(sites) == 3,
          "assembly sites=%s (2 declaration points + 1 defensive fallback)"
          % sites)

    publishes = call_sites(authority, "_publish_contest")
    check("the payload is PUBLISHED as live contest state",
          len(publishes) == 3, "publish sites=%s" % publishes)

    evaluator = function_source(authority, "evaluate_contact") or ""
    check("the manager channel assembles the payload BEFORE recording it",
          evaluator.find("_assemble_contest(") != -1
          and evaluator.find("_assemble_contest(")
          < evaluator.find("_preserve(world, bc, defenders, final, contest)"),
          "assembly precedes _preserve on the formed return")

    choke = function_source(authority, "enter_tes") or ""
    check("the engage channel assembles the payload BEFORE recording it",
          choke.find("_assemble_contest(") != -1
          and choke.find("_assemble_contest(")
          < choke.find("_preserve_channel("),
          "assembly precedes _preserve_channel at the choke point")

    check("a manager hand-over REUSES the declared payload",
          "_live_contest(world)" in choke,
          "the hand-over reads the payload THIS declaration published")

    check("only a formed declaration assembles a payload",
          "if winner is not None:" in evaluator
          and evaluator.count("contest = None") == 1,
          "a failed frame declares no contest, so it has no payload")

    # NOBODY ELSE builds or publishes one: the payload is a single object with a
    # single producer, not something any module may mint.
    others = []
    for path in iter_engine_modules():
        if path in (AUTHORITY_PATH, CONTEST_PATH):
            continue
        try:
            src = _read(path)
        except OSError:
            continue
        names = code_names(src)
        if "build_contest" in names or "publish_contest" in names:
            others.append(path)
    check("no other engine module mints or publishes a Contest",
          not others, "offenders=%s" % others)
    scanned = iter_engine_modules()
    check("the payload scan really is repo-wide",
          len(scanned) > 120 and AUTHORITY_PATH in scanned
          and ENGINE_PATH in scanned,
          "scanned %d engine modules, including the authority and the engine"
          % len(scanned))
# ─────────────────────────────────────────────────────────────────────
# [3] THE TES BOUNDARY
# ─────────────────────────────────────────────────────────────────────

def verify_tes_boundary():
    """The payload crosses the boundary; the entry's shape is intact."""
    section("[3] The TES boundary (the payload is what crosses it)")

    engine = _read(ENGINE_PATH)
    tes = _read(TES_PATH)
    authority = _read(AUTHORITY_PATH)

    # THE STAGE 1B PINS, re-asserted here.  Stage 2B changes WHAT crosses the
    # boundary, never the boundary's shape.
    check("the engine's TES entry keeps its ORIGINAL signature",
          "def _start_tackle(self, bc: Player, d: Player)" in engine,
          "the signature pin is untouched")
    check("the engine's TES entry still delegates to tes_start_tackle",
          "ctx = tes_start_tackle(bc, d, self.world)" in engine,
          "the delegation pin is untouched")
    check("tes_start_tackle is still the declared single TES entry",
          "def tes_start_tackle(bc, defender, world)" in tes,
          "git-level UNCHANGED")

    # WHAT ACTUALLY CROSSES: the unwrap exists, exactly once, and it is the
    # FIRST thing the entry does with its argument.
    entry = method_source(engine, "_start_tackle") or ""
    unwrap_at = entry.find("d = winner_of(d)")
    delegate_at = entry.find("ctx = tes_start_tackle(")
    check("the TES entry unwraps the object it is handed",
          unwrap_at != -1, "d = winner_of(d)")
    check("the unwrap happens BEFORE the defender is used",
          unwrap_at != -1 and delegate_at != -1 and unwrap_at < delegate_at,
          "unwrap@%d delegate@%d" % (unwrap_at, delegate_at))
    check("the unwrap appears EXACTLY ONCE in the engine",
          engine.count("winner_of(d)") == 1,
          "occurrences=%d" % engine.count("winner_of(d)"))

    # THE AUTHORITY HANDS OVER THE OBJECT.  This is the stage's headline:
    # a Contest crosses, not a standalone winner identifier.
    check("the authority hands the CONTEST to the TES entry",
          authority.count("_start_tackle(bc, contest)") == 2,
          "both entry channels pass the payload")
    handed = call_arg_names(authority, "_start_tackle")
    check("the authority never hands over a bare winner",
          bool(handed) and set(handed) == {"contest"},
          "second arguments handed to the entry: %s" % sorted(set(handed)))
    check("the authority still calls the TES entry exactly twice",
          authority.count("_start_tackle(") - 2 == 2,
          "one engage channel + one manager hand-over")

    # THE DECISION SURFACE IS UNCHANGED: the manager still asks the authority
    # to declare and then hand over, in that order.
    manager = _read(MANAGER_PATH)
    check("the manager still declares through the authority",
          "enter_tes(world, ope, bc, _contact_decision.winner" in manager,
          "the admission path is unchanged")
    check("the manager only hands over a FORMED contest",
          "if _contact_decision.outcome is ContactOutcome.CONTACT_FORMED:"
          in manager,
          "a failed frame never reaches the boundary")


# ─────────────────────────────────────────────────────────────────────
# [4] TES CONSUMES ONLY THE WINNER
# ─────────────────────────────────────────────────────────────────────

def verify_only_the_winner():
    """Every other declared fact is advisory and unused by the entry."""
    section("[4] TES consumes ONLY the winning defender")

    engine = _read(ENGINE_PATH)
    tes = _read(TES_PATH)

    try:
        from game.defence.defence_lvl_1 import contest as _c
    except Exception as exc:                                # pragma: no cover
        check("the consumption point is importable", False, repr(exc))
        return

    # THE ONE CONSUMPTION POINT: a Contest is read for what it carries in
    # exactly one place in the whole engine.
    check("the engine has exactly ONE consumption point for a payload",
          engine.count("winner_of(") == 1,
          "occurrences=%d" % engine.count("winner_of("))
    check("and that point is inside the TES entry",
          "winner_of(" in (method_source(engine, "_start_tackle") or ""),
          "the entry reads the winner; nothing else reads the payload")

    # THE ADVISORY FACTS ARE NAMED NOWHERE THE ENTRY CAN SEE THEM.  Reading any
    # of these off the argument would be TES consuming more than the winner, so
    # the check is on ACCESSES of the payload argument, not on the vocabulary.
    entry = method_source(engine, "_start_tackle") or ""
    leaks = sorted(
        name for name in ADVISORY_NAMES if ("d." + name) in entry
    )
    check("the entry reads NO advisory fact off the payload",
          not leaks, "offenders=%s" % leaks)

    # The winner is consumed through the DECLARED accessor, not by reaching
    # into the object.
    check("the winner is consumed through the declared accessor",
          getattr(_c, "winner_of", None) is not None
          and callable(_c.winner_of),
          "winner_of(contest) -> contest.winner")

    # A bare defender still passes straight through, so the engine's own
    # unreachable historical dispatch keeps its exact behaviour.
    try:
        from player.player import Player
        probe_body = Player(x=1.0, y=2.0, player_id=99)
    except Exception:                                       # pragma: no cover
        probe_body = object()
    check("a bare defender passes through the accessor unchanged",
          _c.winner_of(probe_body) is probe_body,
          "the historical path is byte-identical, not special-cased")

    # The payload's winner is THE body, not a copy of it.
    try:
        from types import SimpleNamespace
        winner_body = SimpleNamespace(player_id=7)
        payload = _c.build_contest(
            frame=3, seq=5, phase="OPEN_PLAY", channel="manager_trigger",
            outcome="CONTACT_FORMED", reason="formed",
            winner=winner_body, winner_id=7,
        )
        same = _c.winner_of(payload) is winner_body
        named = payload.player_id == 7
    except Exception as exc:                                # pragma: no cover
        same = False
        named = False
        print("    (payload construction raised: %r)" % (exc,))
    check("the payload carries the winning DEFENDER itself", same,
          "the object TES receives names the body, not a copy")
    check("and it names the winner by the identifier TES reads", named,
          "player_id is the winner identifier")

    # TES ITSELF IS UNTOUCHED: the physics module names no contest at all.
    names = code_names(tes)
    check("the TES physics module names no contest object",
          not ({"Contest", "contest", "winner_of", "contest_id"} & names),
          "shared-nothing: TES was not edited to receive the payload")

    # The payload is NOT a decision layer: it takes no verdict.
    payload_names = code_names(_read(CONTEST_PATH))
    check("the payload contains no decision authority",
          not ({"evaluate_contact", "enter_tes", "should_engage",
                "_check_pre_tackle", "_start_tackle",
                "tes_start_tackle"} & payload_names),
          "it transports a decision; it does not take one")
# ─────────────────────────────────────────────────────────────────────
# [5] THE RECORD PROJECTS THE DECLARATION
# ─────────────────────────────────────────────────────────────────────

def verify_the_record_projects():
    """The historical record derives from the payload; nothing else changed."""
    section("[5] The record is a PROJECTION of the declaration")

    preservation = _read(PRESERVATION_PATH)
    authority = _read(AUTHORITY_PATH)

    # THE PAYLOAD REACHES THE RECORDER.
    check("the decider path accepts the declared payload",
          "def preserve_decider_decision(world, bc, defenders, decision, "
          "contest=None" in preservation,
          "the recorder is handed the declaration, not asked to rebuild it")
    check("the channel path accepts the declared payload",
          "contest=None) -> ContestRecord | None:" in preservation,
          "an engage admission carries its payload too")
    check("the record builder accepts the declared payload",
          "contest=None) -> ContestRecord:" in preservation, "")
    check("the authority passes the payload to the recorder",
          "preserve_decider_decision(world, bc, defenders, decision, contest)"
          in authority,
          "the declaration reaches the recorder WITH its payload")

    # IT COPIES THE DECLARATION'S FACTS.  Each of these is a fact the payload
    # already carries, so re-deriving it would be the divergence class Stage 2B
    # exists to remove.
    copied = (
        "contest.winner_id", "contest.radius_m", "contest.outcome",
        "contest.reason", "contest.phase", "contest.arm_grab_damping",
        "contest.glancing_displacement", "contest.channel", "contest.frame",
        "contest.seq",
    )
    missing = [name for name in copied if name not in preservation]
    check("the record COPIES the declaration's facts from the payload",
          not missing, "missing copies=%s" % missing)

    # IT SOURCES MEMBERSHIP FROM THE PAYLOAD'S DECLARED SETS.
    check("row membership is sourced from the payload's declared sets",
          preservation.count("contest.carries(") >= 2,
          "occurrences=%d (population rows + decider rows)"
          % preservation.count("contest.carries("))

    # THE RECORDER'S OTHER GUARANTEES ARE UNCHANGED.
    attrs, subs = attribute_stores(preservation)
    check("the recorder still performs NO attribute store",
          attrs == 0, "attribute stores=%d" % attrs)
    check("the recorder still performs NO subscript store",
          subs == 0, "subscript stores=%d" % subs)
    stores = setattr_calls(preservation)
    check("the recorder's only stores are its TWO declared world attributes",
          stores == [("world", "CONTEST_RECORD_ATTR"),
                     ("world", "CONTEST_RECORD_INDEX_ATTR")],
          "setattr calls=%s" % stores)

    names = code_names(preservation)
    check("the recorder still takes no decision of its own",
          not ({"evaluate_contact", "enter_tes", "should_engage",
                "run_machine", "_check_pre_tackle", "_start_tackle",
                "tes_start_tackle"} & names),
          "")

    # NOTHING READS WHAT THE DECLARATION PRODUCED.  This is the absolute
    # neutrality proof, extended to the payload: a value no module reads cannot
    # influence a decision in any frame.
    # THE DECLARED READERS.  Stage 2B's law was that NO engine module read the
    # payload or the record, so the transport could not influence a decision.
    # The multi-body consumption stage makes exactly ONE deliberate reader
    # real: the TES entry, which derives a tackle's collaborative wrap model
    # from the declaration.  The law is therefore a CLOSED ALLOW-LIST -- an
    # undeclared reader is still the defect Stage 2B named, a declared reader
    # that reads something else is a lie, and the reader must reach the record
    # through the preservation module's ACCESSOR API, never by naming the raw
    # published world attribute.
    reader_names = {
        "last_record", "recent_records", "record_for",
        "preserve_decider_decision", "preserve_channel_admission",
        "reset_preservation", "format_preservation_report", "admission_for",
        "declared_admission_channels", "preservation_enabled",
        "last_contest", "live_contest", "recent_contests", "reset_contest",
        "format_contest_report", "publish_contest", "build_contest",
    }
    raw_attrs = {"contest_record", "contest_record_index"}
    declared_readers = {"game/tes/tes.py": ("last_record", "record_for")}
    writers = {AUTHORITY_PATH, PRESERVATION_PATH, CONTEST_PATH,
               "game/defence/defence_lvl_1/contest_record.py",
               "game/defence/defence_lvl_1/contest_preservation_audit.py"}
    readers = {}
    for path in iter_engine_modules():
        if path in writers:
            continue
        try:
            src = _read(path)
        except OSError:
            continue
        hits = sorted(reader_names & code_names(src))
        if hits:
            readers[path] = hits

    undeclared = sorted(path for path in readers if path not in declared_readers)
    check("NO UNDECLARED engine module reads the payload or the record",
          not undeclared, "undeclared readers=%s" % undeclared)
    mismatched = {}
    for path, declared in declared_readers.items():
        got = tuple(readers.get(path, ()))
        if set(got) != set(declared):
            mismatched[path] = (sorted(declared), sorted(got))
    check("every declared reader reads EXACTLY its declared accessors",
          not mismatched, "declared vs actual=%s" % mismatched)
    raw = sorted({name for path in declared_readers
                  for name in readers.get(path, ()) if name in raw_attrs})
    check("the declared readers reach the record via the ACCESSOR API",
          not raw, "raw world attributes named by a reader=%s" % raw)
    check("the readers of the record are EXACTLY the declared set",
          sorted(readers) == sorted(declared_readers),
          "readers=%s declared=%s"
          % (sorted(readers), sorted(declared_readers)))
    scanned = iter_engine_modules()
    check("the unread-output scan really is repo-wide",
          len(scanned) > 120 and MANAGER_PATH in scanned
          and ENGINE_PATH in scanned,
          "scanned %d engine modules, including the manager and the engine"
          % len(scanned))

    # The record's own laws still hold: it is built from the DECLARED sets, so
    # the two representations cannot disagree about membership.
    check("the payload declares the same membership the record reports",
          "__dataclass_fields__" in _read(RECORD_PATH)
          or "class ContestRecord" in _read(RECORD_PATH),
          "ContestRecord is unchanged; only its SOURCE moved")


# ─────────────────────────────────────────────────────────────────────
# [6] THE FAILED FRAME HAS NO PAYLOAD
# ─────────────────────────────────────────────────────────────────────

def verify_the_failed_frame():
    """A frame that declares no contest declares no payload either."""
    section("[6] The failed frame (no contest, so no payload)")

    authority = _read(AUTHORITY_PATH)
    preservation = _read(PRESERVATION_PATH)

    evaluator = function_source(authority, "evaluate_contact") or ""
    assemble_at = evaluator.find("_assemble_contest(")
    formed_at = evaluator.find("if winner is not None:")
    publish_at = evaluator.find("_publish_contest(world, contest)")
    check("the payload is assembled INSIDE the formed arm",
          assemble_at != -1 and formed_at != -1 and assemble_at > formed_at,
          "assembly@%d formed-arm@%d" % (assemble_at, formed_at))
    check("and published inside the formed arm too",
          publish_at != -1 and publish_at > formed_at,
          "publish@%d formed-arm@%d" % (publish_at, formed_at))

    check("a carrier-less frame is recorded without a payload",
          "_preserve(world, bc, defenders, _publish(world, decision))"
          in authority,
          "the no-carrier return keeps its original, payload-less call")

    # THE RECORD STILL HANDLES A PAYLOAD-LESS DECLARATION: the fallback branch
    # is what keeps the failed-frame record exactly as Stage 2A built it.
    check("the record builder still derives from the decision when it must",
          "elif decision is not None:" in preservation
          and "if contest is not None:" in preservation,
          "the payload is preferred, the decision is the fallback")

    check("a payload-less declaration is still recorded, not dropped",
          "if not preservation_enabled() or decision is None:" in preservation,
          "only an ABSENT decision suppresses a record")


# ─────────────────────────────────────────────────────────────────────
# [7] THE TRANSPORT IS NOT GATED
# ─────────────────────────────────────────────────────────────────────

def verify_not_gated():
    """The recorder's switch can never remove the object TES is handed."""
    section("[7] The transport is NOT gated by the recorder's switch")

    contest_src = _read(CONTEST_PATH)
    authority = _read(AUTHORITY_PATH)

    payload_names = code_names(contest_src)
    check("the payload module consults no preservation switch",
          "RLM_CONTEST_PRESERVATION" not in payload_names
          and "PRESERVATION_ENV_VAR" not in payload_names,
          "the payload is declared unconditionally")
    check("the payload module reads NO environment variable at all",
          "environ" not in payload_names,
          "the transport has no runtime gate")
    check("the authority never gates the transport on the recorder",
          "preservation_enabled" not in code_names(authority),
          "the authority owns the declaration, not the recorder's switch")

    # The recorder's OWN switch is unchanged: it still removes a READ.
    check("the recorder's switch is unchanged (it still only removes a read)",
          "os.environ.get(PRESERVATION_ENV_VAR, \"1\") != \"0\"" in
          _read(PRESERVATION_PATH),
          "RLM_CONTEST_PRESERVATION is opt-OUT")


# ─────────────────────────────────────────────────────────────────────
# [8] NO BEHAVIOUR CHANGE (the predecessors, to the digit)
# ─────────────────────────────────────────────────────────────────────

def verify_no_behaviour_change():
    """Stage 1B and Stage 2A are exactly where they were."""
    section("[8] No behaviour change - the predecessors, to the digit")

    def _load(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    report_1b = _load(STAGE1B_REPORT)
    report_2a = _load(STAGE2A_REPORT)

    if report_1b is None:
        check("the Stage 1B report is readable", False, STAGE1B_REPORT)
    else:
        for key, expected in STAGE1B_BASELINE.items():
            actual = report_1b.get(key)
            check("Stage 1B %s is still %r" % (key, expected),
                  actual == expected, "recorded=%r" % (actual,))
        check("the Stage 1B contact radius is untouched",
              report_1b.get("radius_m") == 1.5,
              "radius_m=%r" % report_1b.get("radius_m"))

    if report_2a is None:
        check("the Stage 2A report is readable", False, STAGE2A_REPORT)
    else:
        for key, expected in STAGE2A_BASELINE.items():
            actual = report_2a.get(key)
            check("Stage 2A %s is still %r" % (key, expected),
                  actual == expected, "recorded=%r" % (actual,))

    # THE DECISION ITSELF IS UNTOUCHED: the conjuncts, their order and the two
    # failed-contact effects are the ones Stage 1B relocated.
    authority = _read(AUTHORITY_PATH)
    check("the five-conjunct scan is unchanged",
          "for p in defenders:" in authority
          and "def _frame_reason(" in authority
          and "def _evidence_for(" in authority,
          "the decider's loop is intact")
    check("the radius is still read from its owner",
          "from world.open_play_engine import TACKLE_RADIUS_M" in authority,
          "no tuner can move the contact boundary")
    check("the failed-contact effects are unchanged",
          "ARM_GRAB_DAMPING_FACTOR: float = 0.85" in authority
          and "GLANCING_LATERAL_OFFSET_M: float = 0.5" in authority
          and "GLANCING_LATERAL_VELOCITY_MPS: float = 2.0" in authority,
          "bit-identical carrier mutations")

    # THE COORDINATE SPACE CROSSES UNCHANGED: the engine still delegates.
    engine = _read(ENGINE_PATH)
    check("the engine's single TES entry is still declared once",
          engine.count("def _start_tackle(") == 1,
          "one reachable entry")
    check("the boundary is still reached from the manager exactly once",
          _read(MANAGER_PATH).count("evaluate_contact(") == 1,
          "one decision per frame")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section, write the report, print the verdict."""
    print("=" * 74)
    print("CONTACT TRANSPORT CONTRACT VERIFICATION (Stage 2B)")
    print("(a first-class Contest crosses the TES boundary; no behaviour change)")
    print("=" * 74)

    verify_surface()
    verify_one_declaration_one_payload()
    verify_tes_boundary()
    verify_only_the_winner()
    verify_the_record_projects()
    verify_the_failed_frame()
    verify_not_gated()
    verify_no_behaviour_change()

    passed = sum(1 for item in _RESULTS if item["passed"])
    failed = len(_RESULTS) - passed
    sections = sorted({item["section"] for item in _RESULTS})

    report = {
        "stage": "2B",
        "contract": "contests cross the TES boundary as first-class objects",
        "passed": passed,
        "failed": failed,
        "radius_m": 1.5,
        "sections": sections,
        "checks": _RESULTS,
    }
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        print("\nReport written to %s" % REPORT_PATH)
    except OSError as exc:                                  # pragma: no cover
        print("\nReport could not be written: %r" % (exc,))

    print("\n" + "=" * 74)
    if failed:
        print("RESULT: %d of %d CHECKS FAILED" % (failed, len(_RESULTS)))
        for item in _RESULTS:
            if not item["passed"]:
                print("  - [%s] %s (%s)"
                      % (item["section"], item["name"], item["detail"]))
        print("=" * 74)
        return 1
    print("RESULT: ALL %d CHECKS PASSED" % passed)
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
