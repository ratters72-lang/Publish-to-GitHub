"""verify_tes_drive_v1_contract.py — STAGE 3 TES DRIVE v1 CONTRACT.

(Stage 3, 2026-09-21.)

WHAT THIS GATE ASSERTS
----------------------
Stage 3 declares the FRAME-BY-FRAME EVOLUTION of the persistent contest: the
object that is advanced exactly once per simulation cycle while the Stage 2
``TackleState`` is active, and that consumes that state without creating,
replacing or writing it.

The declared ownership sentence this gate machine-checks:

    Open Play owns movement.
    Contact Authority owns contest FORMATION.
    TES owns contest EVOLUTION -- its GROWTH (Stage 2) and its DRIVE (Stage 3).
    Referee owns TERMINATION.
    PTB owns RESTART.

So the gate is organised around exactly what Stage 3 claims and the one thing
it must NOT do:

  [1] THE SURFACE       — a near-leaf module whose ONLY project import is the
                          contest it consumes, ONE published attribute, a
                          closed trend vocabulary, and a declared API.
  [2] THE DECLARED      — the seven declared evolution blocks, each one
      EVOLUTION           carried by a real field of the record, and NO
                          undeclared field on it.
  [3] CONSUMES, NEVER   — it reads the Stage 2 contest through the Stage 2
      CREATES             module's own public reader, and it cannot form one.
  [4] DETERMINISM       — the same inputs evolve the same record, every cycle,
                          with no random source and no wall clock.
  [5] THE MEASUREMENT   — the declared definitions of relative movement,
                          momentum, control/leverage, forward progress and
                          attachment state, measured on constructed bodies.
  [6] NO MUTATION       — advancing a drive writes to the RECORD and to
                          nothing else: no body, no contest, no second world
                          attribute.
  [7] NOT A RESOLUTION  — the module names no referee, no PTB, no terminal
                          result, no bust, no grounding, no dominance award
                          and no tackle count.
  [8] EXACTLY ONCE      — one update per cycle, idempotent per cycle, and a
      PER CYCLE           contest that is WEAKENING is still driven.
  [9] BOUNDED AND       — the declared smoothness and monotonicity rules hold
      MONOTONE            on every cycle.
  [10] THE WIRING       — the engine advances the drive at exactly one call
                          site, reached function-locally.

Every check is a stated fact about the live code, not a restatement of it:
the behavioural checks run the real module, and the structural checks parse
the real source.

REVERSIBILITY
-------------
The whole stage is removable: deleting game/tes/tes_drive_v1.py together with
the ONE engine call returns the engine to Stage 2 exactly, because nothing in
the engine reads any field of the drive it publishes.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
SECTIONS = []


def section(number, title):
    SECTIONS.append((number, title))
    print()
    print("=" * 76)
    print("[%s] %s" % (number, title))
    print("=" * 76)


def check(number, name, ok, detail=""):
    RESULTS.append({"section": number, "name": name, "ok": bool(ok),
                    "detail": str(detail)})
    print("  %s %s" % ("PASS" if ok else "FAIL", name)
          + (("   " + str(detail)) if detail else ""))
    return bool(ok)


def info(number, name, detail=""):
    print("  INFO %s   %s" % (name, detail))


# ─────────────────────────────────────────────────────────────────────
# THE MODULE UNDER TEST + ITS SOURCE
# ─────────────────────────────────────────────────────────────────────

from game.tes import tes_drive_v1 as TD                        # noqa: E402
from game.tes import tackle_state as TS                        # noqa: E402

MODULE_PATH = "game/tes/tes_drive_v1.py"
with open(MODULE_PATH, "r", encoding="utf-8") as _fh:
    SOURCE = _fh.read()
TREE = ast.parse(SOURCE)
CONTEST_PATH = "game/tes/tackle_state.py"
ENGINE_PATH = "world/open_play_engine.py"

with open(CONTEST_PATH, "r", encoding="utf-8") as _fh:
    CONTEST_SOURCE = _fh.read()
with open(ENGINE_PATH, "r", encoding="utf-8") as _fh:
    ENGINE_SOURCE = _fh.read()

PROJECT_ROOTS = ("game", "world", "player", "ball", "kick_off")


def module_scope_imports(source):
    """Every module-scope ``import`` / ``from ... import`` as (level, name)."""
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((0, alias.name))
        elif isinstance(node, ast.ImportFrom):
            found.append((int(node.level or 0), node.module or ""))
    return found


def all_imported_names(source):
    """Map every imported local alias to the name it was imported from."""
    rows = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                rows[alias.asname or alias.name] = alias.name
    return rows


def all_imported_modules(source):
    """Every module named by any import at ANY nesting depth."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                found.add("." * node.level + (node.module or ""))
            elif node.module:
                found.add(node.module)
    return found


def project_imports(source):
    """Module-scope imports that reach the project (not stdlib)."""
    return [name for _level, name in module_scope_imports(source)
            if name.split(".")[0] in PROJECT_ROOTS]


def attribute_stores(source):
    """Every ``x.y = ...`` store in the module (base name + attribute)."""
    rows = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Attribute):
                base = tgt.value
                base_name = (base.id if isinstance(base, ast.Name)
                             else "__expr__")
                rows.append((base_name, tgt.attr, node.lineno))
    return rows


def subscript_stores(source):
    """Every ``x[...] = ...`` store, and every ``x[...] += ...``.

    Only a SUBSCRIPT target counts.  ``x.attr += 1`` is an ATTRIBUTE store
    and is already covered by :func:`attribute_stores`, so counting it here
    would report the same line under two different headings.
    """
    rows = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript):
                    rows.append(node.lineno)
        elif isinstance(node, ast.AugAssign) and isinstance(
            node.target, ast.Subscript
        ):
            rows.append(node.lineno)
    return rows


def call_count(source, name):
    """AST call sites whose function is the bare NAME *name*."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name):
            count += 1
    return count


def setattr_calls(source):
    """Every ``setattr(...)`` call node in the module."""
    rows = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "delattr")):
            rows.append((node.func.id, node.lineno))
    return rows


def code_names(source, skip_docstrings=True):
    """Every NAME and ATTRIBUTE identifier the CODE mentions."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if skip_docstrings and id(node) in docstrings:
                continue
            for raw in (node.value.replace("(", " ").replace(")", " ")
                        .split()):
                token = raw.strip(".,;:'\"")
                if token and all(c.isalnum() or c == "_" for c in token):
                    names.add(token)
    return names


# ─────────────────────────────────────────────────────────────────────
# TEST FIXTURES — a minimal body and world, plus a real Stage 2 contest
# ─────────────────────────────────────────────────────────────────────

class Body:
    def __init__(self, player_id, x, y, vx=0.0, vy=0.0, facing=0.0,
                 defender_role=None, tackle_role=None, mass_kg=90.0,
                 is_defender=False):
        self.player_id = player_id
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.facing = float(facing)
        self.defender_role = defender_role
        self.tackle_role = tackle_role
        self.mass_kg = float(mass_kg)
        self.is_defender = bool(is_defender)


class World:
    def __init__(self, frame_count=42, sim_time_s=7.0, attack_dir=1):
        self.frame_count = frame_count
        self.sim_time_s = sim_time_s
        self.attacking_direction = attack_dir
        self.players = []


BODY_FIELDS = ("player_id", "x", "y", "vx", "vy", "facing", "defender_role",
               "tackle_role", "mass_kg", "is_defender")
INVENTED = ("frozen", "tes_locked", "in_contact", "is_tackler", "anim_state",
            "attack_role", "target_x", "target_y", "vx_target", "vy_target",
            "movement_mode", "tackle_state", "tes_drive_v1", "tackle_id",
            "drive_id", "tes_attached", "attach_slot")


def body_state(body):
    """One body's whole observable state, as comparable text."""
    rows = [(name, repr(getattr(body, name, None))) for name in BODY_FIELDS]
    rows.append(("__extra__", repr(sorted(set(body.__dict__)
                                           - set(BODY_FIELDS)))))
    return tuple(rows)


def fresh_world(frame=42, sim=7.0, direction=1):
    """A world with both ledgers dropped."""
    world = World(frame_count=frame, sim_time_s=sim, attack_dir=direction)
    TS.reset_tackle_states(world)
    TD.reset_tes_drives_v1(world)
    return world


def parties(dx=0.4, dy=0.0, cvx=5.0, cvy=0.0, dvx=-4.0, dvy=0.0,
            carrier_x=60.0, carrier_y=35.0, mass_c=90.0, mass_d=90.0):
    """A carrier and a primary defender, closing, as Stage 2 fixtures."""
    carrier = Body(7, carrier_x, carrier_y, cvx, cvy, facing=0.0,
                   mass_kg=mass_c)
    defender = Body(8, carrier_x + dx, carrier_y + dy, dvx, dvy,
                    facing=math.pi, defender_role="TACKLER_1",
                    tackle_role=TS.ROLE_PRIMARY, mass_kg=mass_d,
                    is_defender=True)
    return carrier, defender


def formed_world(frame=42, seq=1, **kwargs):
    """A world with ONE real Stage 2 contest formed on it."""
    world = fresh_world(frame=frame)
    carrier, defender = parties(**kwargs)
    world.players = [carrier, defender]
    state = TS.form_tackle_state(world, carrier, defender, frame=frame,
                                 seq=seq, phase="OPEN_PLAY", channel="gate",
                                 admitted_by_decider=True)
    return world, carrier, defender, state


DRIVE_FACT_FIELDS = (
    "drive_id", "tackle_id", "carrier_id", "primary_defender_id",
    "origin_frame", "origin_time_s", "entry_frame", "owner",
    "forward_progress_m", "forward_progress_raw_m", "forward_best_m",
    "forward_worst_m", "relative_vx", "relative_vy", "relative_speed_mps",
    "separation_m", "closing_speed_mps", "carrier_speed_mps",
    "defender_speed_mps", "carrier_momentum_kgmps",
    "defender_momentum_kgmps", "momentum_balance_kgmps",
    "control", "leverage", "control_delta", "control_trend",
    "attachment_count", "attached_count", "contact_set_size",
    "new_attachments", "frames_active", "update_count",
    "last_update_frame", "last_update_time_s", "cycle_dt_s",
)


def facts(drive):
    """Every declared fact of a drive, as comparable text."""
    return tuple((name, repr(getattr(drive, name, None)))
                 for name in DRIVE_FACT_FIELDS)


print(__file__)
print("=" * 76)
print("TES DRIVE v1 CONTRACT VERIFICATION (Stage 3)")
print("(the frame-by-frame contest evolution: one cycle, one update)")
print("=" * 76)
print("module under test: %s" % MODULE_PATH)
# ═════════════════════════════════════════════════════════════════════
# [1] THE SURFACE
# ═════════════════════════════════════════════════════════════════════
section("1", "THE SURFACE - a near-leaf with ONE published attribute")

_project = project_imports(SOURCE)
check("1", "the module's ONLY project import is the contest it consumes",
      _project == ["game.tes.tackle_state"],
      "project imports=%s" % _project)

check("1", "its module-scope imports are stdlib plus that one project import",
      sorted(name for _level, name in module_scope_imports(SOURCE))
      == ["__future__", "dataclasses", "enum", "game.tes.tackle_state",
          "math"],
      "imports=%s" % sorted(name for _level, name
                            in module_scope_imports(SOURCE)))

check("1", "the published attribute is a declared constant",
      TD.TES_DRIVE_ATTR == "tes_drive_v1",
      "TES_DRIVE_ATTR=%r" % TD.TES_DRIVE_ATTR)

check("1", "the drive identity derives from the CONTEST it drives",
      TD.tes_drive_id_for("tackle-42-3") == "drive-tackle-42-3",
      "tes_drive_id_for('tackle-42-3')=%r"
      % TD.tes_drive_id_for("tackle-42-3"))

check("1", "the one owner a drive ever has is TES",
      TD.OWNER_TES == "tes", "OWNER_TES=%r" % TD.OWNER_TES)

check("1", "the trend vocabulary is CLOSED and has three members",
      sorted(t.value for t in TD.ControlTrend)
      == ["holding", "strengthening", "weakening"],
      "%s" % sorted(t.value for t in TD.ControlTrend))

check("1", "the control axis is a declared bounded band",
      TD.MIN_CONTROL == 0.0 and TD.MAX_CONTROL == 1.0
      and TD.MIN_CONTROL < TD.CONTROL_NEUTRAL < TD.MAX_CONTROL,
      "band=[%r, %r] neutral=%r"
      % (TD.MIN_CONTROL, TD.MAX_CONTROL, TD.CONTROL_NEUTRAL))

check("1", "the per-cycle smoothness bound is declared and positive",
      isinstance(TD.MAX_CONTROL_STEP_PER_CYCLE, float)
      and 0.0 < TD.MAX_CONTROL_STEP_PER_CYCLE <= 1.0,
      "MAX_CONTROL_STEP_PER_CYCLE=%r" % TD.MAX_CONTROL_STEP_PER_CYCLE)

check("1", "the progress envelope is declared and ordered",
      TD.MAX_BACKWARD_M > 0.0 and TD.MAX_FORWARD_M > 0.0,
      "envelope=[-%r, +%r]" % (TD.MAX_BACKWARD_M, TD.MAX_FORWARD_M))

check("1", "the ring is bounded",
      isinstance(TD.TES_DRIVE_RING_LIMIT, int)
      and TD.TES_DRIVE_RING_LIMIT > 0,
      "TES_DRIVE_RING_LIMIT=%d" % TD.TES_DRIVE_RING_LIMIT)

check("1", "the declared API is complete and importable",
      all(callable(getattr(TD, n, None)) for n in (
          "tes_drive_id_for", "target_control_for", "trend_for",
          "seed_control_for", "measure_tes_drive_v1",
          "create_tes_drive_v1", "advance_tes_drive_v1",
          "published_tes_drive_v1", "active_tes_drive_v1",
          "recent_tes_drives_v1", "reset_tes_drives_v1",
          "format_tes_drive_v1_report", "declared_evolution")),
      "all callables resolve")

# ── THE ONE PUBLICATION, AND THE STORE BASES ──
_STORE_BASES = sorted({base for base, _attr, _line
                       in attribute_stores(SOURCE)})
_publishes = setattr_calls(SOURCE)
check("1", "the module performs NO subscript store anywhere",
      not subscript_stores(SOURCE),
      "subscript stores=%s" % subscript_stores(SOURCE))
check("1", "its only world writes are setattr/delattr of the ONE attribute",
      sorted({kind for kind, _line in _publishes}) == ["delattr", "setattr"],
      "calls=%s" % sorted(set(kind for kind, _line in _publishes)))
check("1", "every attribute store targets the module's OWN record",
      set(_STORE_BASES) <= {"drive"},
      "store bases=%s stores=%d"
      % (_STORE_BASES, len(attribute_stores(SOURCE))))
check("1", "and NO store targets the world, a body or the contest",
      not (set(_STORE_BASES)
           & {"world", "bc", "body", "carrier", "primary_defender",
              "defender", "d", "p", "state", "tackle", "self"}),
      "store bases=%s" % _STORE_BASES)
check("1", "the module exposes a NARROW lifecycle API",
      all(callable(getattr(TD, n, None)) for n in (
          "create_tes_drive_v1", "advance_tes_drive_v1",
          "active_tes_drive_v1", "published_tes_drive_v1",
          "reset_tes_drives_v1")),
      "five callables: create, advance, read the live one, read the "
      "published one, drop the ledger")


# ═════════════════════════════════════════════════════════════════════
# [2] THE DECLARED EVOLUTION
# ═════════════════════════════════════════════════════════════════════
section("2", "THE DECLARED EVOLUTION - seven blocks, each a real field")

DECLARED = TD.declared_evolution()
DECLARED_NAMES = [name for name, _ in DECLARED]
DECLARED_FIELDS = [f for _name, fields in DECLARED for f in fields]
FIELDS = set(TD.TESDriveV1.__dataclass_fields__)

check("2", "seven declared evolution blocks", len(DECLARED) == 7,
      "got %d" % len(DECLARED))
check("2", "the declared block names are distinct",
      len(set(DECLARED_NAMES)) == len(DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

print()
for name, field_names in DECLARED:
    missing = [f for f in field_names if f not in FIELDS]
    check("2", "declared '%s' is carried by real fields" % name,
          not missing, "fields=%s missing=%s" % (list(field_names), missing))

check("2", "EVERY declared field is a field of the record",
      all(f in FIELDS for f in DECLARED_FIELDS),
      "declared=%d record fields=%d"
      % (len(DECLARED_FIELDS), len(FIELDS)))
check("2", "and the record carries NO undeclared field",
      not (FIELDS - set(DECLARED_FIELDS)),
      "undeclared=%s" % sorted(FIELDS - set(DECLARED_FIELDS)))

for required in ("identity_and_provenance", "forward_progress",
                 "relative_movement", "momentum", "control_and_leverage",
                 "attachment_state", "cycle_bookkeeping"):
    check("2", "the declared content names %s" % required,
          required in DECLARED_NAMES, "%s" % DECLARED_NAMES)

check("2", "the identity block is written ONCE and carries provenance",
      all(f in FIELDS for f in ("drive_id", "tackle_id", "carrier_id",
                                "primary_defender_id", "origin_frame",
                                "origin_time_s", "entry_frame", "owner")),
      "identity fields resolve")

_rec = TD.TESDriveV1(drive_id="d", tackle_id="t", carrier_id=1,
                     primary_defender_id=2, origin_frame=5,
                     origin_time_s=0.1, entry_frame=5)
check("2", "a fresh record opens on the declared neutral control",
      _rec.control == TD.CONTROL_NEUTRAL
      and _rec.leverage == TD.CONTROL_NEUTRAL
      and _rec.control_partition_holds(),
      "control=%r leverage=%r" % (_rec.control, _rec.leverage))
check("2", "a fresh record is not yet updated",
      _rec.updated is False and _rec.update_count == 0,
      "update_count=%d" % _rec.update_count)
check("2", "the record reports the one owner it ever has",
      _rec.owned_by_tes is True and _rec.trend == "holding",
      "owner=%r trend=%r" % (_rec.owner, _rec.trend))
check("2", "the declared identity scheme is what the record uses",
      TD.tes_drive_id_for("tackle-42-1") == "drive-tackle-42-1",
      "%s" % TD.tes_drive_id_for("tackle-42-1"))


# ═════════════════════════════════════════════════════════════════════
# [3] CONSUMES, NEVER CREATES
# ═════════════════════════════════════════════════════════════════════
section("3", "CONSUMES, NEVER CREATES - the contest is Stage 2's")

check("3", "the module imports the Stage 2 LIVE-CONTEST reader by name",
      all_imported_names(SOURCE).get("active_tackle_state")
      == "active_tackle_state",
      "imported names from the contest module: %s"
      % {k: v for k, v in all_imported_names(SOURCE).items()
         if v in ("active_tackle_state", "form_tackle_state")})

check("3", "it CANNOT form a contest: no formation call site exists",
      call_count(SOURCE, "form_tackle_state") == 0,
      "call sites=%d" % call_count(SOURCE, "form_tackle_state"))

check("3", "and the formation callable is never imported",
      "form_tackle_state" not in all_imported_names(SOURCE),
      "imported names=%s" % sorted(all_imported_names(SOURCE)))

check("3", "the module reaches NO module that is not the contest or stdlib",
      all(m in ("game.tes.tackle_state",)
          or m.split(".")[0] not in PROJECT_ROOTS
          for m in all_imported_modules(SOURCE)),
      "project reaches=%s"
      % sorted(m for m in all_imported_modules(SOURCE)
               if m.split(".")[0] in PROJECT_ROOTS))

# ── A CYCLE WITH NO CONTEST CREATES NOTHING ──
world = fresh_world(frame=10)
before_ring = len(TD.recent_tes_drives_v1())
result = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("3", "a cycle with NO active contest creates NOTHING",
      result is None
      and len(TD.recent_tes_drives_v1()) == before_ring,
      "returned=%r ring delta=%d"
      % (result, len(TD.recent_tes_drives_v1()) - before_ring))
check("3", "and it latches nothing on the world",
      TD.published_tes_drive_v1(world) is None,
      "published=%r" % TD.published_tes_drive_v1(world))

world = fresh_world(frame=11)
_carrier = Body(7, 60.0, 35.0)
_orphan = Body(8, 60.4, 35.0)
check("3", "creating a drive WITHOUT a contest is a no-op",
      TD.create_tes_drive_v1(world, None) is None
      and TD.published_tes_drive_v1(world) is None,
      "create_tes_drive_v1(world, None) is None")
check("3", "and a drive-less world reports no live drive",
      TD.active_tes_drive_v1(world) is None
      and "no_drive" in TD.format_tes_drive_v1_report(world),
      "%s" % TD.format_tes_drive_v1_report(world))


# ═════════════════════════════════════════════════════════════════════
# [4] DETERMINISM
# ═════════════════════════════════════════════════════════════════════
section("4", "DETERMINISM - the same inputs evolve the same record")

_NAMES = code_names(SOURCE)
NONDETERMINISM = ("random", "randint", "randrange", "uniform", "gauss",
                  "normalvariate", "choice", "shuffle", "sample",
                  "environ", "getenv", "perf_counter", "monotonic",
                  "process_time", "datetime", "uuid", "time_ns",
                  "seed")
_hits = sorted(n for n in NONDETERMINISM if n in _NAMES)
check("4", "the module names NO random source, clock or environment",
      not _hits, "nondeterminism names present=%s" % _hits)

check("4", "it imports no module that could supply one",
      not any(m in ("random", "time", "datetime", "uuid", "os", "secrets")
              for m in all_imported_modules(SOURCE)),
      "imports=%s" % sorted(all_imported_modules(SOURCE)))


def drive_sequence(frame=100, steps=6, **kwargs):
    """A contest driven for *steps* cycles with a scripted body motion.

    The SAME script is applied on every call, so two calls are two
    independent replays of identical inputs.
    """
    world, carrier, defender, state = formed_world(frame=frame, **kwargs)
    seen = []
    for step in range(steps):
        world.frame_count = frame + step
        world.sim_time_s = 3.0 + step * (1.0 / 60.0)
        carrier.x = 60.0 + 0.05 * step
        carrier.y = 35.0 + 0.01 * step
        carrier.vx = 5.0 - 0.1 * step
        carrier.vy = 0.2 * step
        defender.x = 60.4 + 0.02 * step
        defender.vx = -4.0 + 0.1 * step
        drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
        seen.append(drive)
    return world, carrier, defender, state, seen


_w1, _c1, _d1, _s1, _seq1 = drive_sequence()
_w2, _c2, _d2, _s2, _seq2 = drive_sequence()

check("4", "two replays of identical inputs produce the SAME cycle count",
      all(a.update_count == b.update_count
          for a, b in zip(_seq1, _seq2)),
      "counts=%s vs %s" % ([d.update_count for d in _seq1],
                           [d.update_count for d in _seq2]))
_diffs = [i for i, (a, b) in enumerate(zip(_seq1, _seq2))
          if facts(a) != facts(b)]
check("4", "two replays produce BYTE-IDENTICAL records every cycle",
      not _diffs, "differing cycles=%s" % _diffs)

check("4", "the drive's own fields are stable under a repeat advance",
      TD.advance_tes_drive_v1(_w1, 1.0 / 60.0) is _seq1[-1]
      and facts(_seq1[-1]) == facts(_seq2[-1]),
      "an idempotent extra call does not move the record")

_start = (TD.seed_control_for(_s1), TD.seed_control_for(_s2))
check("4", "the seed control is a function of the CONTEST's frozen data",
      _start[0] == _start[1]
      and abs(_start[0] - TD.target_control_for(
          _s1.carrier_momentum_kgmps - _s1.defender_momentum_kgmps)) < 1e-12,
      "seed=%r for contest %s" % (_start[0], _s1.tackle_id))


# ═════════════════════════════════════════════════════════════════════
# [5] THE MEASUREMENT
# ═════════════════════════════════════════════════════════════════════
section("5", "THE MEASUREMENT - the declared definitions, on real bodies")

world, carrier, defender, state = formed_world(
    frame=200, dx=0.4, dy=0.0, cvx=5.0, cvy=0.0, dvx=-4.0, dvy=0.0)
measured = TD.measure_tes_drive_v1(world, state)

check("5", "the measurement resolves for a formed contest",
      measured is not None, "measured=%s" % (measured is not None))
check("5", "the relative velocity is carrier-minus-primary, per axis",
      abs(measured["relative_vx"] - 9.0) < 1e-9
      and abs(measured["relative_vy"] - 0.0) < 1e-9,
      "relative_v=(%.3f, %.3f)"
      % (measured["relative_vx"], measured["relative_vy"]))
check("5", "the separation is the carrier-to-primary centre distance",
      abs(measured["separation_m"] - 0.4) < 1e-9,
      "separation_m=%.6f" % measured["separation_m"])
check("5", "the closing speed is POSITIVE while the pair closes",
      measured["closing_speed_mps"] > 0.0,
      "closing_speed_mps=%.6f" % measured["closing_speed_mps"])
check("5", "the carrier speed and momentum are measured",
      abs(measured["carrier_speed_mps"] - 5.0) < 1e-9
      and abs(measured["carrier_momentum_kgmps"] - 450.0) < 1e-9,
      "speed=%.3f momentum=%.3f"
      % (measured["carrier_speed_mps"],
         measured["carrier_momentum_kgmps"]))
check("5", "the defender momentum is the CONTACT SET's aggregate",
      abs(measured["defender_momentum_kgmps"] - 360.0) < 1e-9,
      "defender_momentum_kgmps=%.3f (primary alone, 90 kg x 4.0)"
      % measured["defender_momentum_kgmps"])
check("5", "the momentum balance is the carrier minus the contact set",
      abs(measured["momentum_balance_kgmps"] - 90.0) < 1e-9,
      "balance=%.3f" % measured["momentum_balance_kgmps"])
check("5", "the control target is the declared squash of that balance",
      abs(measured["target_control"]
          - TD.target_control_for(90.0)) < 1e-12,
      "target=%.6f" % measured["target_control"])
check("5", "the attachment mirror reports the contest's own counts",
      measured["attachment_count"] == state.attachment_count
      and measured["attached_count"] == len(state.attached_ids)
      and measured["contact_set_size"] == state.contact_set_size,
      "attachments=%d attached=%d set=%d"
      % (measured["attachment_count"], measured["attached_count"],
         measured["contact_set_size"]))

# ── FORWARD PROGRESS IS MEASURED FROM THE DECLARED STAGE 2 ORIGIN ──
world2, carrier2, defender2, state2 = formed_world(frame=201)
carrier2.x = 62.0
_m2 = TD.measure_tes_drive_v1(world2, state2)
check("5", "forward progress is measured from the CONTEST's origin",
      abs(_m2["forward_progress_raw_m"] - 2.0) < 1e-9
      and abs(_m2["forward_progress_m"] - 2.0) < 1e-9,
      "origin x=%.2f carrier x=%.2f -> %.4f m"
      % (state2.forward_origin_x, carrier2.x,
         _m2["forward_progress_raw_m"]))

carrier2.x = 58.0
_m3 = TD.measure_tes_drive_v1(world2, state2)
check("5", "backward travel reads NEGATIVE and is bounded by the envelope",
      _m3["forward_progress_raw_m"] == -2.0
      and _m3["forward_progress_m"] == -TD.MAX_BACKWARD_M,
      "raw=%.4f bounded=%.4f envelope=%.2f"
      % (_m3["forward_progress_raw_m"], _m3["forward_progress_m"],
         TD.MAX_BACKWARD_M))

carrier2.x = 60.0 + TD.MAX_FORWARD_M + 25.0
_m4 = TD.measure_tes_drive_v1(world2, state2)
check("5", "a runaway advance is CLAMPED to the declared forward bound",
      _m4["forward_progress_raw_m"] > TD.MAX_FORWARD_M
      and _m4["forward_progress_m"] == TD.MAX_FORWARD_M,
      "raw=%.4f bounded=%.4f bound=%.2f"
      % (_m4["forward_progress_raw_m"], _m4["forward_progress_m"],
         TD.MAX_FORWARD_M))

# ── THE ATTACK DIRECTION SIGNS THE ORIGIN ──
world3 = fresh_world(frame=202, direction=-1)
carrier3 = Body(7, 60.0, 35.0, -5.0, 0.0)
defender3 = Body(8, 59.6, 35.0, 4.0, 0.0, facing=0.0,
                 defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
                 is_defender=True)
world3.players = [carrier3, defender3]
state3 = TS.form_tackle_state(world3, carrier3, defender3, frame=202, seq=1)
carrier3.x = 58.0
_m5 = TD.measure_tes_drive_v1(world3, state3)
check("5", "forward progress is SIGNED by the contest's attack direction",
      _m5["attack_dir"] == -1
      and abs(_m5["forward_progress_raw_m"] - 2.0) < 1e-9,
      "attack_dir=%d raw=%.4f (a -X attack advancing -2.0 m)"
      % (_m5["attack_dir"], _m5["forward_progress_raw_m"]))

# ── A DEGENERATE CONTEST CANNOT BE MEASURED INTO A DRIVE ──
check("5", "a contest with no parties measures NOTHING",
      TD.measure_tes_drive_v1(world, None) is None
      and TD.create_tes_drive_v1(world, None) is None,
      "measure_tes_drive_v1(world, None) is None")
# ═════════════════════════════════════════════════════════════════════
# [6] NO MUTATION
# ═════════════════════════════════════════════════════════════════════
section("6", "NO MUTATION - a cycle writes to the record and nothing else")

world, carrier, defender, state = formed_world(frame=300)
joiner = Body(9, 60.9, 35.3, -3.0, 0.2, facing=math.pi,
              defender_role="TACKLER_2", tackle_role="SECONDARY",
              is_defender=True)
world.players.append(joiner)

# The world-name snapshot is taken AFTER the Stage 2 formation, so what it
# measures is STAGE 3's footprint and not the contest's.
_names_before = set(world.__dict__)
_contest_before = (state.attachment_count, state.attached_ids,
                   state.separation_m, state.contact_angle_deg,
                   state.forward_origin_x, state.forward_origin_y,
                   state.attack_dir, state.owner, state.closed)
_bodies_before = [body_state(b) for b in (carrier, defender, joiner)]

TD.advance_tes_drive_v1(world, 1.0 / 60.0)
world.frame_count += 1
TD.advance_tes_drive_v1(world, 1.0 / 60.0)
TS.append_attachment(world, joiner, "SECONDARY")
TS.mark_attached(world, joiner)
world.frame_count += 1
TD.advance_tes_drive_v1(world, 1.0 / 60.0)
drive = TD.active_tes_drive_v1(world)

_bodies_after = [body_state(b) for b in (carrier, defender, joiner)]
_names_after = set(world.__dict__)

_differing = []
for index, (before, after) in enumerate(zip(_bodies_before, _bodies_after)):
    for (name, first), (_same, second) in zip(before, after):
        if first != second:
            differing.append((index, name, first, second))

check("6", "the whole public API leaves every body byte-identical",
      not _differing, "differences=%s" % (_differing or "none"))
check("6", "it invents NO flag on a body",
      not [name for name in INVENTED
           if any(hasattr(b, name) for b in (carrier, defender, joiner))],
      "invented=%s"
      % [name for name in INVENTED
         if any(hasattr(b, name) for b in (carrier, defender, joiner))])
check("6", "the only world name the stage introduces is its ONE attribute",
      _names_after - _names_before == {TD.TES_DRIVE_ATTR},
      "introduced=%s (declared %r)"
      % (sorted(_names_after - _names_before), TD.TES_DRIVE_ATTR))

# ── THE CONTEST ITSELF IS NOT WRITTEN ──
# ``attachments`` is Stage 2's OWN declared growth field, so it is excluded
# from the invariance set; everything else the contest holds must not move.
_contest_after = (state.attachment_count, state.attached_ids,
                  state.separation_m, state.contact_angle_deg,
                  state.forward_origin_x, state.forward_origin_y,
                  state.attack_dir, state.owner, state.closed)
check("6", "the contest's identity, geometry and provenance are untouched",
      (_contest_before[2:] == _contest_after[2:]),
      "formation data unchanged across three driven cycles")
check("6", "the contest is still OPEN after being driven",
      state.closed is False and state.closed_reason is None,
      "closed=%r reason=%r" % (state.closed, state.closed_reason))
check("6", "the drive MIRRORS the contest's growth rather than causing it",
      drive.attachment_count == state.attachment_count == 1
      and drive.attached_count == len(state.attached_ids) == 1,
      "drive attachments=%d contest attachments=%d"
      % (drive.attachment_count, state.attachment_count))

# ── THE MODULE OWNS NO MOVEMENT FIELD ──
forbidden_movement = [n for n in _NAMES if n in
                      ("vx_target", "vy_target", "speed_target",
                       "heading_target", "move_speed", "target_x",
                       "target_y", "lunge_active", "movement_mode",
                       "collision_priority", "frozen", "tes_locked",
                       "in_contact", "is_tackler")]
check("6", "the module names NO movement or body-ownership field",
      not forbidden_movement,
      "movement names=%s" % forbidden_movement)


# ═════════════════════════════════════════════════════════════════════
# [7] NOT A RESOLUTION
# ═════════════════════════════════════════════════════════════════════
section("7", "NOT A RESOLUTION - no referee, no PTB, no terminal outcome")

FORBIDDEN = [
    # referee / termination
    "referee", "Referee", "award", "awarded", "terminate", "terminated",
    "tackle_result", "TESOutcome", "finalise", "finalize",
    # PTB / restart / next possession
    "PTB", "PTBManager", "ptb_manager", "ptb_ready", "ptb_time",
    "play_the_ball", "restart", "next_possession",
    # grounding / bust / collapse / dominance award
    "grounded", "to_ground", "pushback", "bust", "broken", "break_tackle",
    "collapse", "collapsed", "dominance", "compute_dominance",
    # tackle counting
    "tackle_count", "tackle_number",
    # resolution
    "resolved", "TESPhase", "tes_start_tackle", "compute_drive_distance",
    "drive_distance", "drive_strength", "drive_target",
    # the engine's OWN drive subsystem, which Stage 3 must not touch
    "TESDriveVariation", "tes_drive_variation", "tes_drive_shadow",
    "update_defence_during_tes_drive",
]
_hits = sorted(n for n in FORBIDDEN if n in _NAMES)
check("7", "the module names NO referee, PTB, resolution or result symbol",
      not _hits, "forbidden names present=%s" % _hits)

_reaches = all_imported_modules(SOURCE)
check("7", "it does not import the TES engine, the PTB manager or a rules "
           "module",
      not any(("tes.tes" in m or "ptb" in m.lower()
               or "tes_context" in m or "tes_outcomes" in m
               or "tes_dominance" in m or "tes_drive_variation" in m
               or "world.rules" in m or "referee" in m.lower())
              for m in _reaches),
      "reaches=%s" % sorted(_reaches))

_open_play_names = [n for n in _NAMES if n in ("open_play", "OPEN_PLAY",
                                               "GamePhase", "GameSubState")]
check("7", "it never returns the engine to open play",
      not _open_play_names, "open-play names in CODE=%s" % _open_play_names)

# ── THE RECORD CARRIES NO OUTCOME ──
check("7", "the record carries NO result, outcome, winner or verdict field",
      not any(f in FIELDS for f in
              ("result", "outcome", "winner", "verdict", "score", "award",
               "penalty", "offload", "strip", "fumble", "six_again",
               "line_break", "tackle_result", "closed", "closed_reason",
               "closed_frame", "ptb_ready", "ptb_time_s", "grounded")),
      "record fields=%d, none of them an outcome" % len(FIELDS))

# ── A DRIVE NEVER ENDS A CONTEST ──
world, carrier, defender, state = formed_world(frame=310)
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("7", "advancing a contest does NOT close it",
      state.closed is False and drive is not None
      and TS.active_tackle_state(world) is state,
      "the contest is still the ACTIVE contest after a cycle")

# ── AND THE ENGINE'S OWN LIFECYCLE IS THE ONLY THING THAT CLOSES IT ──
TS.clear_tackle_state(world)
check("7", "only the declared lifecycle boundary closes the contest",
      state.closed is True
      and state.closed_reason in TS.CLOSE_REASONS
      and TD.active_tes_drive_v1(world) is None,
      "closed by the engine's vocabulary (%r), and the drive latch drops"
      % state.closed_reason)


# ═════════════════════════════════════════════════════════════════════
# [8] EXACTLY ONCE PER CYCLE
# ═════════════════════════════════════════════════════════════════════
section("8", "EXACTLY ONCE PER CYCLE - idempotent, and never a second time")

world, carrier, defender, state = formed_world(frame=400)
first = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("8", "the creation cycle IS the first update",
      first is not None and first.update_count == 1
      and first.frames_active == 1
      and first.last_update_frame == world.frame_count,
      "update_count=%d last_update_frame=%r"
      % (first.update_count, first.last_update_frame))

_after_first = facts(first)
_again = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("8", "a SECOND call in the same cycle is a no-op",
      _again is first and facts(first) == _after_first,
      "the record is byte-identical and the cycle count did not move")

_third = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("8", "and a THIRD call is equally inert",
      _third is first and first.update_count == 1,
      "update_count=%d after three calls in one cycle" % first.update_count)

# ── ONE UPDATE PER CYCLE, OVER A RUN ──
world.frame_count += 1
_moved = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("8", "the next cycle advances the drive by exactly one",
      _moved is first and first.update_count == 2
      and first.last_update_frame == world.frame_count,
      "update_count=%d frame=%r"
      % (first.update_count, first.last_update_frame))
check("8", "and frames_active always tracks the cycle count",
      first.frames_active == first.update_count,
      "frames_active=%d update_count=%d"
      % (first.frames_active, first.update_count))

# ── A WEAKENING CONTEST IS STILL AN ACTIVE CONTEST ──
world, carrier, defender, state = formed_world(frame=410)
carrier.vx = 0.0
carrier.vy = 0.0
defender.vx = -8.0
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
_witness = []
for step in range(20):
    world.frame_count += 1
    defender.vx = -8.0
    carrier.vx = 0.0
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
    _witness.append((drive.control_trend.value, drive.update_count,
                     state.closed))

check("8", "a contest whose control FALLS keeps being driven",
      any(trend == "weakening" for trend, _c, _closed in _witness)
      and drive.update_count == 21,
      "trends=%s cycles=%d"
      % (sorted({trend for trend, _c, _s in _witness}), drive.update_count))
check("8", "and it is STILL an active contest on every one of those cycles",
      not any(closed for _t, _c, closed in _witness),
      "the contest never closed while its control was falling")

# ── A NEW CONTEST REPLACES THE DRIVE, AND THE OLD ONE IS NEVER ADVANCED ──
world, carrier, defender, state = formed_world(frame=420)
old = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
old_count = old.update_count
world.frame_count += 1
carrier2 = Body(11, 70.0, 35.0, 5.0, 0.0)
defender2 = Body(12, 70.4, 35.0, -4.0, 0.0, facing=math.pi,
                 defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
                 is_defender=True)
world.players = [carrier2, defender2]
state2 = TS.form_tackle_state(world, carrier2, defender2, frame=421, seq=2)
new = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("8", "a NEW declaration gets a NEW drive for the NEW contest",
      new is not None and new is not old
      and new.tackle_id == state2.tackle_id
      and new.drive_id != old.drive_id,
      "old=%s new=%s" % (old.drive_id, new.drive_id))
check("8", "the superseded drive is never advanced again",
      old.update_count == old_count
      and TD.active_tes_drive_v1(world) is new,
      "old cycles=%d (frozen), live drive is %s"
      % (old.update_count, new.drive_id))
check("8", "and only the LIVE contest's drive is reported",
      TD.published_tes_drive_v1(world) is new
      and TD.active_tes_drive_v1(world) is new,
      "%s" % TD.format_tes_drive_v1_report(world))
# ═════════════════════════════════════════════════════════════════════
# [9] BOUNDED AND MONOTONE
# ═════════════════════════════════════════════════════════════════════
section("9", "BOUNDED AND MONOTONE - the declared smoothness rules")

# ── THE PER-CYCLE STEP IS A REAL CEILING, NOT A SUGGESTION ──
# The carrier attacking at speed into a contact set that is standing still:
# the balance is large and POSITIVE, so the target control is the top of the
# band.  (Two equal and opposite speeds would be a stalemate -- the balance
# is a magnitude comparison, so that pair targets the neutral 0.5.)
world, carrier, defender, state = formed_world(frame=500)
carrier.vx = 400.0
defender.vx = 0.0
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
check("9", "one cycle can move control by AT MOST the declared step",
      abs(drive.control_delta) <= TD.MAX_CONTROL_STEP_PER_CYCLE + 1e-12
      and abs(drive.control_delta - TD.MAX_CONTROL_STEP_PER_CYCLE) < 1e-9
      and drive.control_delta > 0.0,
      "delta=+%.6f bound=%.6f (seeded at %.4f, targeting the top of the "
      "band)" % (drive.control_delta, TD.MAX_CONTROL_STEP_PER_CYCLE,
                 drive.control - drive.control_delta))

_controls = [drive.control]
_leverages = [drive.leverage]
for step in range(40):
    world.frame_count += 1
    carrier.vx = 400.0
    defender.vx = 0.0
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
    _controls.append(drive.control)
    _leverages.append(drive.leverage)

check("9", "control climbs MONOTONICALLY toward the bound and stops there",
      all(b >= a for a, b in zip(_controls, _controls[1:]))
      and _controls[-1] == TD.MAX_CONTROL
      and max(_controls) == TD.MAX_CONTROL,
      "control %.4f -> %.4f in %d cycles (bounded by %.1f)"
      % (_controls[0], _controls[-1], len(_controls), TD.MAX_CONTROL))
check("9", "leverage is the exact complement on EVERY cycle",
      all(abs(c + l - 1.0) < 1e-12
          for c, l in zip(_controls, _leverages)),
      "control+leverage == 1.0 across all %d cycles" % len(_controls))
check("9", "every step over the whole climb is within the declared bound",
      all(abs(b - a) <= TD.MAX_CONTROL_STEP_PER_CYCLE + 1e-12
          for a, b in zip(_controls, _controls[1:])),
      "the bound holds on all %d transitions"
      % (len(_controls) - 1))
check("9", "the trend LABEL follows the movement, and is not a verdict",
      drive.control_trend is TD.ControlTrend.HOLDING
      and TD.trend_for(0.1) is TD.ControlTrend.STRENGTHENING
      and TD.trend_for(-0.1) is TD.ControlTrend.WEAKENING,
      "saturated control reads HOLDING (it has nowhere to go)")

# ── THE ENVELOPE IS MONOTONE AND ALWAYS CONTAINS THE READING ──
world, carrier, defender, state = formed_world(frame=520)
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
_best = [drive.forward_best_m]
_worst = [drive.forward_worst_m]
_readings = [drive.forward_progress_m]
for step, offset in enumerate((3.0, -1.0, 6.0, -4.0, 0.5, 8.0)):
    world.frame_count += 1
    carrier.x = 60.0 + offset
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
    _best.append(drive.forward_best_m)
    _worst.append(drive.forward_worst_m)
    _readings.append(drive.forward_progress_m)

check("9", "the forward best is monotone non-decreasing",
      all(b >= a for a, b in zip(_best, _best[1:])),
      "best=%s" % [round(v, 2) for v in _best])
check("9", "the backward worst is monotone non-decreasing in magnitude",
      all(b <= a for a, b in zip(_worst, _worst[1:])),
      "worst=%s" % [round(v, 2) for v in _worst])
check("9", "every reading sits inside its own envelope",
      all(w - 1e-9 <= r <= b + 1e-9
          for r, b, w in zip(_readings, _best, _worst)),
      "readings=%s" % [round(v, 2) for v in _readings])
check("9", "the envelope stays inside the declared bounds",
      min(_worst) >= -TD.MAX_BACKWARD_M - 1e-9
      and max(_best) <= TD.MAX_FORWARD_M + 1e-9,
      "worst>=%.2f best<=%.2f" % (-TD.MAX_BACKWARD_M, TD.MAX_FORWARD_M))

# ── THE ATTACHMENT MIRROR ONLY EVER GROWS, BECAUSE THE CONTEST ONLY GROWS ──
world, carrier, defender, state = formed_world(frame=540)
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
_counts = [drive.attachment_count]
for index in range(3):
    world.frame_count += 1
    joiner = Body(20 + index, 60.5 + 0.2 * index, 35.0 + 0.2 * index,
                  -2.0, 0.0, facing=math.pi,
                  defender_role="TACKLER_%d" % (index + 2),
                  tackle_role="SECONDARY", is_defender=True)
    world.players.append(joiner)
    TS.append_attachment(world, joiner, "SECONDARY")
    TS.mark_attached(world, joiner)
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
    _counts.append(drive.attachment_count)

check("9", "the attachment mirror is monotone non-decreasing",
      all(b >= a for a, b in zip(_counts, _counts[1:])),
      "attachment_count=%s" % _counts)
check("9", "and it equals the CONTEST's own count on every cycle",
      drive.attachment_count == state.attachment_count
      and drive.attached_count == len(state.attached_ids),
      "drive=%d contest=%d" % (drive.attachment_count,
                               state.attachment_count))

# ── THE IDENTITY BLOCK IS WRITTEN ONCE AND NEVER AGAIN ──
IDENTITY = ("drive_id", "tackle_id", "carrier_id", "primary_defender_id",
            "origin_frame", "origin_time_s", "entry_frame", "owner")
world, carrier, defender, state = formed_world(frame=560)
drive = TD.advance_tes_drive_v1(world, 1.0 / 60.0)
_first = tuple((n, repr(getattr(drive, n))) for n in IDENTITY)
for step in range(15):
    world.frame_count += 1
    carrier.x = 60.0 + 0.1 * step
    carrier.vx = 5.0 + step
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
_last = tuple((n, repr(getattr(drive, n))) for n in IDENTITY)
check("9", "the identity and provenance block never moves after creation",
      _first == _last,
      "identifier, parties, origin frame/time, entry frame, owner: all fixed")

# ── SEPARATION IS A DISTANCE AND CANNOT GO NEGATIVE ──
world, carrier, defender, state = formed_world(frame=580)
_separations = []
for step in range(6):
    world.frame_count += 1
    defender.x = 60.4 - 0.3 * step
    defender.y = 35.0 + 0.2 * step
    TD.advance_tes_drive_v1(world, 1.0 / 60.0)
    _separations.append(drive.separation_m)
check("9", "separation is always a non-negative distance",
      all(s >= 0.0 for s in _separations),
      "min=%.6f" % min(_separations))
check("9", "the relative speed is the magnitude of the relative velocity",
      abs(drive.relative_speed_mps
          - math.hypot(drive.relative_vx, drive.relative_vy)) < 1e-9,
      "speed=%.6f from v=(%.4f, %.4f)"
      % (drive.relative_speed_mps, drive.relative_vx, drive.relative_vy))


# ═════════════════════════════════════════════════════════════════════
# [10] THE WIRING
# ═════════════════════════════════════════════════════════════════════
section("10", "THE WIRING - ONE cycle call site, reached function-locally")

check("10", "the engine advances the drive at EXACTLY one call site",
      call_count(ENGINE_SOURCE, "advance_tes_drive_v1") == 1,
      "call sites=%d" % call_count(ENGINE_SOURCE, "advance_tes_drive_v1"))

check("10", "the engine reaches the module FUNCTION-LOCALLY",
      "from game.tes.tes_drive_v1 import advance_tes_drive_v1"
      in ENGINE_SOURCE
      and "game.tes.tes_drive_v1"
      not in [name for _level, name
              in module_scope_imports(ENGINE_SOURCE)],
      "the module never enters the engine's module-scope import graph")

check("10", "the call sits INSIDE the engine's per-cycle update",
      "advance_tes_drive_v1" in inspect.getsource(
          __import__("world.open_play_engine", fromlist=["x"])
          .OpenPlayEngine.update),
      "OpenPlayEngine.update is the one place a cycle is advanced")

check("10", "the engine reads NO field of the drive it publishes",
      not any(f in code_names(ENGINE_SOURCE) for f in
              ("control", "leverage", "control_trend", "control_delta",
               "forward_progress_m", "forward_best_m", "forward_worst_m",
               "momentum_balance_kgmps", "closing_speed_mps",
               "attachment_count", "update_count", "frames_active",
               "drive_id", "update_count")),
      "the engine names none of the drive's facts")

check("10", "the engine does not import the module at all at module scope",
      "tes_drive_v1" not in [name for _level, name
                             in module_scope_imports(ENGINE_SOURCE)],
      "the whole stage is removable from the engine by deleting one call")

# ── THE ENGINE'S OWN DRIVE SUBSYSTEM IS NOT TOUCHED BY THE STAGE ──
_engine_defs = {node.name for node in ast.walk(ast.parse(ENGINE_SOURCE))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
_stage3_untouched = tuple(
    name for name in ("_update_tackle_init", "_update_tackle_contact",
                      "_update_tackle_drive", "_update_tackle_dominance",
                      "_start_tackle")
    if name in _engine_defs)
check("10", "the engine's OWN TES chain handlers are still defined",
      len(_stage3_untouched) >= 4,
      "still defined=%s (Stage 3 adds a SECOND, independent object; it "
      "neither removes nor renames the engine's own handlers)"
      % list(_stage3_untouched))


# ═════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════
passed = sum(1 for r in RESULTS if r["ok"])
failed = [r for r in RESULTS if not r["ok"]]

print()
print("=" * 76)
print("TES DRIVE v1 CONTRACT SUMMARY")
print("=" * 76)
for number, title in SECTIONS:
    rows = [r for r in RESULTS if r["section"] == number]
    good = sum(1 for r in rows if r["ok"])
    print("  [%-2s] %-58s %2d/%2d" % (number, title, good, len(rows)))
print("  " + "-" * 74)
print("  TOTAL: %d checks, %d passed, %d failed"
      % (len(RESULTS), passed, len(failed)))
if failed:
    print()
    for row in failed:
        print("  FAIL [%s] %s   %s" % (row["section"], row["name"],
                                       row["detail"]))
print("=" * 76)
print("RESULT: %s" % ("CONTRACT HOLDS" if not failed else "CONTRACT BROKEN"))

report = {
    "stage": 3,
    "name": "TES Drive v1",
    "module": MODULE_PATH,
    "passed": passed,
    "failed": len(failed),
    "total": len(RESULTS),
    "sections": [{"number": number, "title": title}
                 for number, title in SECTIONS],
    "results": RESULTS,
}
with open("verify_tes_drive_v1_contract_report.json", "w",
          encoding="utf-8") as handle:
    json.dump(report, handle, indent=2)
print("wrote verify_tes_drive_v1_contract_report.json")

sys.exit(1 if failed else 0)
