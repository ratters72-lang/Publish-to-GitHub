"""verify_contest_record_schema_contract.py — THE PURE-DATA LEAF'S CONTRACT.

The STATIC gate for ``game/defence/defence_lvl_1/contest_record.py``: the
structure of the record itself, and the fact that a mocked multi-body frame
resolves onto that structure without throwing away the row the authority
never reached.

WHAT THIS PROVES, AND HOW IT DIFFERS FROM ITS SIBLING
-----------------------------------------------------
``verify_contest_preservation_contract.py`` (Stage 2A) proves the STAGE's
behavioural laws on the LIVE authority: L1-L5, neutrality, the audit sink and
the five declaration sites.  This file proves the LEAF's own shape, which that
gate does not assert anywhere:

  * the field SET, field ORDER, defaults and frozen-ness of the three
    dataclasses;
  * the ANNOTATION STRINGS, which exist because the leaf declares
    ``from __future__ import annotations`` — so ``dataclasses.fields()[i].type``
    is a STRING, not a type.  A schema test that compared types directly would
    silently compare strings to types and never fail.  This gate asserts the
    strings AND that ``typing.get_type_hints`` resolves them to real types, and
    that the two AGREE about which fields admit ``None``;
  * the DERIVED VIEWS, which are properties and therefore invisible to
    ``dataclasses.fields()`` — a shape test built only on ``fields()`` cannot
    see ``formed`` / ``evaluated`` / ``in_radius`` / ``winner`` at all;
  * a MOCKED three-role proximity frame in which a primary defender wins the
    authority, an adjacent role holder sits INSIDE the 1.5 m radius and is
    never examined because the decider short-circuits, and a final role holder
    stays outside the radius — asserting that every one of the record's 21
    fields is populated and that the unexamined row survives as
    ``IN_RADIUS_NOT_EVALUATED``.

The mocked frame is driven through the REAL ``contact_authority.evaluate_contact``
with a scripted ``_check_pre_tackle``, so the short-circuit is the engine's own
``break`` and not a simulation of one.  The decider's predicate-call log is
asserted, which is what makes "never examined" a measured fact rather than a
disposition we simply declared.

SECTIONS
--------
  [1] THE LEAF ......... stdlib-only imports, ``__all__``, frozen-ness.
  [2] THE VOCABULARY ... the closed six, their order, the declared subsets.
  [3] THE SCHEMA ....... field counts, field order, defaults, factories.
  [4] THE ANNOTATIONS .. the string types, their resolution, None-agreement.
  [5] THE DERIVED VIEWS  not fields, are properties, and their semantics.
  [6] THE MOCKED FRAME . 21 fields populated on a real declaration.
  [7] THE MAPPING ...... each body's disposition, and the discarded row.
  [8] THE ROW MATRIX ... all 20 row fields, and independent recomputation.
  [9] THE POSITIONAL PROFILE (Stage 4) ... the jersey -> physics mapper and
      the LIVE slots it fills, so the record's trait fields carry a declared
      positional profile instead of the extractor's neutral 1.0 fallback.

Run:    python verify_contest_record_schema_contract.py
Writes: verify_contest_record_schema_contract_report.json
        tmp/verify_contest_record_schema_contract.log
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import os
import sys
import typing

sys.path.insert(0, ".")

RECORD_MODULE = "game/defence/defence_lvl_1/contest_record.py"
LOG_PATH = "tmp/verify_contest_record_schema_contract.log"
REPORT_PATH = "verify_contest_record_schema_contract_report.json"

# The leaf may import NOTHING but these: it is the shape of the record and has
# no engine reach.  Stated as an exact set, not a blacklist, so a NEW import
# cannot slip in unnoticed.
_LEAF_ALLOWED_IMPORTS = frozenset({"dataclasses", "enum", "__future__"})

# The exact declaration order of the leaf's public surface.
_LEAF_ALL = (
    "CandidateDisposition",
    "EVALUATED_DISPOSITIONS",
    "WINNER_DISPOSITIONS",
    "IN_RADIUS_ONLY_DISPOSITION",
    "OUT_OF_RADIUS_DISPOSITION",
    "CarrierSnapshot",
    "CandidateEvidence",
    "ContestRecord",
)

# The exact declaration order of the closed vocabulary.
_DISPOSITION_ORDER = (
    "EVALUATED_WINNER",
    "EVALUATED_REJECTED",
    "IN_RADIUS_NOT_EVALUATED",
    "ADMITTED_BY_CHANNEL_PREDICATE",
    "ROLE_HOLDER_OUT_OF_RADIUS",
    "NOT_A_CANDIDATE",
)

_DISPOSITION_VALUES = {
    "EVALUATED_WINNER": "evaluated_winner",
    "EVALUATED_REJECTED": "evaluated_rejected",
    "IN_RADIUS_NOT_EVALUATED": "in_radius_not_evaluated",
    "ADMITTED_BY_CHANNEL_PREDICATE": "admitted_by_channel_predicate",
    "ROLE_HOLDER_OUT_OF_RADIUS": "role_holder_out_of_radius",
    "NOT_A_CANDIDATE": "not_a_candidate",
}

# (name, declared annotation STRING) in declaration order.  Measured from the
# live dataclasses, not transcribed from prose.
_CARRIER_TYPES = (
    ("player_id", "int"),
    ("x", "float"),
    ("y", "float"),
    ("vx", "float"),
    ("vy", "float"),
    # Stage 3.1 — the carrier's two skill-trait PLACEHOLDERS.  Both are
    # `float` and both are NEUTRAL (1.0) on every body in the tree today;
    # they are declared here so the cardinality gate cannot silently fall
    # behind the schema it exists to pin down.
    ("fend_trait", "float"),
    ("tackle_break_trait", "float"),
)

_ROW_TYPES = (
    ("player_id", "int"),
    ("defender_role", "str"),
    ("tackle_role", "str | None"),
    ("depth_class", "str | None"),
    ("dist", "float"),
    ("in_radius", "bool"),
    ("disposition", "CandidateDisposition"),
    ("failed_at", "str | None"),
    ("predicate_verdict", "str | None"),
    ("pre_tackle_state", "str | None"),
    ("corridor_owned", "bool"),
    ("corridor_frames", "int"),
    ("slot_eligible", "bool | None"),
    ("engage_guard", "bool"),
    ("engage_guard_stamped", "bool"),
    ("engage_state_last", "str | None"),
    ("retreat_obligated", "bool"),
    ("is_winner", "bool"),
    # Stage 3.1 — the body's two skill-trait PLACEHOLDERS, APPENDED LAST so
    # every field a Stage 2A reader already addresses keeps its position.
    ("tackle_skill_trait", "float"),
    ("core_strength_trait", "float"),
)

_RECORD_TYPES = (
    ("frame", "int"),
    ("seq", "int"),
    ("phase", "str"),
    ("outcome", "str"),
    ("reason", "str"),
    ("channel", "str"),
    ("admission_predicate", "str"),
    ("admission_site", "str"),
    ("decider", "str"),
    ("winner_id", "int | None"),
    ("radius_m", "float"),
    ("candidate_count", "int"),
    ("role_holder_count", "int"),
    ("in_radius_count", "int"),
    ("evaluated_count", "int"),
    ("candidates", "tuple"),
    ("carrier", "CarrierSnapshot"),
    ("arm_grab_damping", "bool"),
    ("glancing_displacement", "float | None"),
    ("population_source", "str"),
    ("record_index", "int"),
)

# The six record fields that declare a default, and the one that uses a
# factory.  Everything before ``candidates`` is required positionally.
_RECORD_DEFAULTED = (
    "candidates",
    "carrier",
    "arm_grab_damping",
    "glancing_displacement",
    "population_source",
    "record_index",
)

# Derived readers on ContestRecord: present as properties/methods, and
# therefore ABSENT from ``dataclasses.fields()``.
_RECORD_VIEWS = (
    "formed",
    "evaluated",
    "in_radius",
    "un_evaluated_in_radius",
    "winner",
    "evaluated_ids",
    "in_radius_ids",
    "un_evaluated_in_radius_ids",
)

# Derived readers on CandidateEvidence.
_ROW_VIEWS = ("evaluated", "never_evaluated")

# The mocked frame.  The carrier sits at (60.0, 35.0); the declared contact
# radius is 1.5 m.
BC_X, BC_Y = 60.0, 35.0
CONTACT_RADIUS_M = 1.5
# player_id -> (x, y, scripted verdict for _check_pre_tackle)
_BODY_10_REJECTED = 10
_BODY_11_WINNER = 11
_BODY_12_UNEXAMINED = 12
_BODY_14_OUT_OF_RADIUS = 14
_MOCK_LINE = (
    (_BODY_10_REJECTED, 59.6, 35.0, "MISS"),
    (_BODY_11_WINNER, 59.7, 35.0, "CONTACT"),
    (_BODY_12_UNEXAMINED, 59.8, 35.0, None),
    (_BODY_14_OUT_OF_RADIUS, 55.0, 35.0, None),
)
_MOCK_VERDICTS = {_BODY_10_REJECTED: "MISS", _BODY_11_WINNER: "CONTACT"}

# The disposition each mocked body must resolve to.
_EXPECTED_DISPOSITION = {
    _BODY_10_REJECTED: "EVALUATED_REJECTED",
    _BODY_11_WINNER: "EVALUATED_WINNER",
    _BODY_12_UNEXAMINED: "IN_RADIUS_NOT_EVALUATED",
    _BODY_14_OUT_OF_RADIUS: "ROLE_HOLDER_OUT_OF_RADIUS",
}

# The exact values the record's scalar fields must carry on the mocked frame.
_RECORD_EXPECTED = {
    "frame": 21,
    "seq": 1,
    "phase": "OPEN_PLAY",
    "outcome": "CONTACT_FORMED",
    "reason": "formed",
    "channel": "manager_trigger",
    "admission_predicate": "world.open_play_engine._check_pre_tackle",
    "admission_site": "game.managers.defence_manager.update_open_play",
    "decider": "contact_authority.evaluate_contact",
    "winner_id": 11,
    "radius_m": 1.5,
    "candidate_count": 4,
    "role_holder_count": 4,
    "in_radius_count": 3,
    "evaluated_count": 2,
    "arm_grab_damping": False,
    "glancing_displacement": None,
    "population_source": "caller",
    "record_index": 1,
}

RESULTS = []


class _Tee:
    """Mirror every write to a second stream, so the run is logged to disk.

    The harness (``run_fb_verifiers.py``) redirects stdout to a temporary file
    it deletes afterwards, so the only durable record of a run is what this
    mirror writes.  Nothing here writes outside the working directory and the
    log is opened with an explicit encoding.
    """

    def __init__(self, primary, mirror):
        self._primary = primary
        self._mirror = mirror

    def write(self, text):
        """Write to the console stream and the log, never failing on either."""
        written = 0
        try:
            written = self._primary.write(text)
        except Exception:
            written = len(text)
        try:
            self._mirror.write(text)
        except Exception:
            pass
        return written

    def flush(self):
        """Flush both streams."""
        for stream in (self._primary, self._mirror):
            try:
                stream.flush()
            except Exception:
                pass

    def reconfigure(self, **kwargs):
        """Forward ``reconfigure`` so later callers keep working."""
        target = getattr(self._primary, "reconfigure", None)
        if target is not None:
            target(**kwargs)

    @property
    def encoding(self):
        """The console stream's encoding."""
        return getattr(self._primary, "encoding", "utf-8")

    def isatty(self):
        """Never a terminal: the output is a log."""
        return False


def _install_log():
    """Send stdout and stderr to ``LOG_PATH`` as well as the console."""
    os.makedirs("tmp", exist_ok=True)
    handle = open(LOG_PATH, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)
    return handle


def check(section: str, name: str, ok, detail=""):
    """Record one check result and print it."""
    ok = bool(ok)
    RESULTS.append({
        "section": section,
        "name": name,
        "passed": ok,
        "detail": str(detail),
    })
    print("  %s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def info(label: str, detail):
    """Print one informational measurement."""
    print("  INFO  %-52s %s" % (label, detail))


def read_source(path: str) -> str:
    """The full source of *path*."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def field_type_map(model) -> tuple:
    """``((name, declared_string), ...)`` in declaration order for *model*."""
    return tuple((field.name, field.type)
                 for field in dataclasses.fields(model))


def imported_roots(src: str) -> set:
    """Every ROOT module name *src* imports (``a.b.c`` -> ``a``)."""
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            roots.add(module.split(".")[0] if module else "<relative>")
        elif isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
    return roots


def _allows_none(annotation) -> bool:
    """True when *annotation* admits ``None``."""
    return type(None) in typing.get_args(annotation)
# ─────────────────────────────────────────────────────────────────────
# [1] THE LEAF
# ─────────────────────────────────────────────────────────────────────

def section_leaf():
    """The leaf's imports, its public surface, and its frozen-ness."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[1] THE LEAF - stdlib only, eight names, frozen instances")

    roots = imported_roots(read_source(RECORD_MODULE))
    check("1", "the leaf imports ONLY its declared stdlib roots",
          roots == set(_LEAF_ALLOWED_IMPORTS),
          "imports=%s" % sorted(roots))
    check("1", "the leaf reaches no engine package",
          not (roots & {"game", "world", "player", "ball"}),
          "so it is the shape of the record, not a subsystem")
    info("declared imports", sorted(roots))

    check("1", "__all__ is exactly the eight declared names, in order",
          tuple(cr.__all__) == _LEAF_ALL, "%s" % (list(cr.__all__),))
    check("1", "every name in __all__ resolves on the module",
          all(hasattr(cr, name) for name in _LEAF_ALL))
    check("1", "no stdlib helper leaks into the public surface",
          not ({"dataclass", "field", "Enum", "annotations"} & set(cr.__all__)),
          "field/dataclass/Enum stay private to the leaf")

    for model in (cr.CarrierSnapshot, cr.CandidateEvidence, cr.ContestRecord):
        check("1", "%s is a FROZEN dataclass" % model.__name__,
              dataclasses.is_dataclass(model)
              and model.__dataclass_params__.frozen,
              "a published record cannot be edited after the fact")

    snapshot = cr.CarrierSnapshot(player_id=1, x=0.0, y=0.0, vx=0.0, vy=0.0,
                                  fend_trait=1.0, tackle_break_trait=1.0)
    refused = False
    try:
        snapshot.x = 9.9
    except dataclasses.FrozenInstanceError:
        refused = True
    except Exception:
        refused = False
    check("1", "a frozen instance REFUSES attribute assignment", refused,
          "assigning .x raised FrozenInstanceError" if refused
          else "assignment was ACCEPTED")

    zero = cr.CarrierSnapshot.of(None)
    check("1", "CarrierSnapshot.of(None) is the declared all-zero snapshot",
          zero == cr.CarrierSnapshot(player_id=-1, x=0.0, y=0.0,
                                     vx=0.0, vy=0.0,
                                     fend_trait=1.0, tackle_break_trait=1.0),
          "%s" % (zero,))
    check("1", "the id-less snapshot carries the NEUTRAL trait multipliers",
          zero.fend_trait == 1.0 and zero.tackle_break_trait == 1.0,
          "fend=%s break=%s — the identity of a percentage multiplier"
          % (zero.fend_trait, zero.tackle_break_trait))

    carried = cr.CarrierSnapshot.of(snapshot)
    check("1", "CarrierSnapshot.of() copies a real carrier verbatim",
          carried == snapshot
          and carried is not snapshot,
          "a snapshot, never the live body")


# ─────────────────────────────────────────────────────────────────────
# [2] THE VOCABULARY
# ─────────────────────────────────────────────────────────────────────

def section_vocabulary():
    """The closed disposition set, its declaration order, its subsets."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[2] THE VOCABULARY - the closed six and the declared subsets")
    members = list(cr.CandidateDisposition)

    check("2", "the disposition vocabulary is CLOSED and has six members",
          len(members) == 6, "got %d" % len(members))
    check("2", "the DECLARATION ORDER is exactly the declared six",
          tuple(member.name for member in members) == _DISPOSITION_ORDER,
          "%s" % (tuple(member.name for member in members),))
    check("2", "every member's value is exactly the declared string",
          all(member.value == _DISPOSITION_VALUES[member.name]
              for member in members))
    check("2", "the six values are distinct",
          len({member.value for member in members}) == 6,
          "%s" % sorted(member.value for member in members))

    check("2", "EVALUATED_DISPOSITIONS is exactly the three deciding outcomes",
          cr.EVALUATED_DISPOSITIONS == frozenset({
              cr.CandidateDisposition.EVALUATED_WINNER,
              cr.CandidateDisposition.EVALUATED_REJECTED,
              cr.CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE}))
    check("2", "WINNER_DISPOSITIONS is exactly the two admitting outcomes",
          cr.WINNER_DISPOSITIONS == frozenset({
              cr.CandidateDisposition.EVALUATED_WINNER,
              cr.CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE}))
    check("2", "EVALUATED is WINNER plus the rejected row",
          cr.EVALUATED_DISPOSITIONS
          == cr.WINNER_DISPOSITIONS
          | {cr.CandidateDisposition.EVALUATED_REJECTED})
    check("2", "the winner set is a SUBSET of the evaluated set",
          cr.WINNER_DISPOSITIONS <= cr.EVALUATED_DISPOSITIONS)

    check("2", "IN_RADIUS_ONLY_DISPOSITION is a single member, not a set",
          cr.IN_RADIUS_ONLY_DISPOSITION
          is cr.CandidateDisposition.IN_RADIUS_NOT_EVALUATED
          and not isinstance(cr.IN_RADIUS_ONLY_DISPOSITION, frozenset),
          "%s" % (cr.IN_RADIUS_ONLY_DISPOSITION,))
    check("2", "OUT_OF_RADIUS_DISPOSITION is a single member, not a set",
          cr.OUT_OF_RADIUS_DISPOSITION
          is cr.CandidateDisposition.ROLE_HOLDER_OUT_OF_RADIUS
          and not isinstance(cr.OUT_OF_RADIUS_DISPOSITION, frozenset),
          "%s" % (cr.OUT_OF_RADIUS_DISPOSITION,))

    check("2", "the two geometric dispositions are NOT deciding outcomes",
          cr.IN_RADIUS_ONLY_DISPOSITION not in cr.EVALUATED_DISPOSITIONS
          and cr.OUT_OF_RADIUS_DISPOSITION not in cr.EVALUATED_DISPOSITIONS,
          "radius is geometry; the disposition is what was done")
    check("2", "NOT_A_CANDIDATE is in NEITHER declared set",
          cr.CandidateDisposition.NOT_A_CANDIDATE
          not in cr.EVALUATED_DISPOSITIONS
          and cr.CandidateDisposition.NOT_A_CANDIDATE
          not in cr.WINNER_DISPOSITIONS,
          "a body never in the running was not decided about")


# ─────────────────────────────────────────────────────────────────────
# [3] THE SCHEMA
# ─────────────────────────────────────────────────────────────────────

def section_schema():
    """Field counts, field order, defaults and default factories."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[3] THE SCHEMA - 21 / 20 / 7, in declaration order")

    record_fields = dataclasses.fields(cr.ContestRecord)
    row_fields = dataclasses.fields(cr.CandidateEvidence)
    carrier_fields = dataclasses.fields(cr.CarrierSnapshot)

    check("3", "ContestRecord declares exactly 21 fields",
          len(record_fields) == 21, "got %d" % len(record_fields))
    check("3", "CandidateEvidence declares exactly 20 fields",
          len(row_fields) == 20, "got %d" % len(row_fields))
    check("3", "CarrierSnapshot declares exactly 7 fields",
          len(carrier_fields) == 7, "got %d" % len(carrier_fields))
    check("3", "the trailing row fields ARE the two trait placeholders",
          tuple(f.name for f in row_fields)[-2:]
          == ("tackle_skill_trait", "core_strength_trait"),
          "appended LAST, so no Stage 2A reader's position moved")
    check("3", "the trailing carrier fields ARE the two trait placeholders",
          tuple(f.name for f in carrier_fields)[-2:]
          == ("fend_trait", "tackle_break_trait"))

    check("3", "ContestRecord's fields are in the declared order",
          tuple(f.name for f in record_fields)
          == tuple(name for name, _ in _RECORD_TYPES),
          "%s" % (tuple(f.name for f in record_fields),))
    check("3", "CandidateEvidence's fields are in the declared order",
          tuple(f.name for f in row_fields)
          == tuple(name for name, _ in _ROW_TYPES),
          "%s" % (tuple(f.name for f in row_fields),))
    check("3", "CarrierSnapshot's fields are in the declared order",
          tuple(f.name for f in carrier_fields)
          == tuple(name for name, _ in _CARRIER_TYPES))

    defaulted = tuple(
        f.name for f in record_fields
        if f.default is not dataclasses.MISSING
        or f.default_factory is not dataclasses.MISSING)
    check("3", "exactly the six declared record fields carry a default",
          defaulted == _RECORD_DEFAULTED, "defaulted=%s" % (defaulted,))

    factories = tuple(f.name for f in record_fields
                      if f.default_factory is not dataclasses.MISSING)
    check("3", "exactly ONE record field uses a default_factory",
          factories == ("carrier",), "factories=%s" % (factories,))

    required = tuple(f.name for f in record_fields
                     if f.default is dataclasses.MISSING
                     and f.default_factory is dataclasses.MISSING)
    check("3", "the required record fields are exactly the first fifteen",
          required == tuple(name for name, _ in _RECORD_TYPES[:15]),
          "%d required" % len(required))

    candidates_field = next(f for f in record_fields
                            if f.name == "candidates")
    check("3", "the candidates default is the EMPTY tuple, not a list",
          candidates_field.default == ()
          and isinstance(candidates_field.default, tuple))
    check("3", "the arm_grab_damping default is False",
          next(f for f in record_fields
               if f.name == "arm_grab_damping").default is False)
    check("3", "the glancing_displacement default is None",
          next(f for f in record_fields
               if f.name == "glancing_displacement").default is None)
    check("3", "the population_source default is 'none'",
          next(f for f in record_fields
               if f.name == "population_source").default == "none")
    check("3", "the record_index default is 0",
          next(f for f in record_fields
               if f.name == "record_index").default == 0)

    check("3", "NO CandidateEvidence field carries a default",
          all(f.default is dataclasses.MISSING
              and f.default_factory is dataclasses.MISSING
              for f in row_fields),
          "a body row is measured, never defaulted")
    check("3", "NO CarrierSnapshot field carries a default",
          all(f.default is dataclasses.MISSING
              and f.default_factory is dataclasses.MISSING
              for f in carrier_fields))


# ─────────────────────────────────────────────────────────────────────
# [4] THE ANNOTATIONS
# ─────────────────────────────────────────────────────────────────────

def section_annotations():
    """The string annotations, their resolution, and their None agreement."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[4] THE ANNOTATIONS - strings on the field, types on the hint")

    for model in (cr.ContestRecord, cr.CandidateEvidence, cr.CarrierSnapshot):
        check("4", "every %s annotation is a STRING" % model.__name__,
              all(isinstance(f.type, str)
                  for f in dataclasses.fields(model)),
              "the leaf declares 'from __future__ import annotations'")

    check("4", "ContestRecord's declared type strings are exactly these",
          field_type_map(cr.ContestRecord) == _RECORD_TYPES,
          "%d strings" % len(_RECORD_TYPES))
    check("4", "CandidateEvidence's declared type strings are exactly these",
          field_type_map(cr.CandidateEvidence) == _ROW_TYPES,
          "%d strings" % len(_ROW_TYPES))
    check("4", "CarrierSnapshot's declared type strings are exactly these",
          field_type_map(cr.CarrierSnapshot) == _CARRIER_TYPES)

    record_hints = typing.get_type_hints(cr.ContestRecord)
    row_hints = typing.get_type_hints(cr.CandidateEvidence)
    carrier_hints = typing.get_type_hints(cr.CarrierSnapshot)

    check("4", "get_type_hints resolves every ContestRecord annotation",
          set(record_hints) == set(name for name, _ in _RECORD_TYPES),
          "%d resolved" % len(record_hints))
    check("4", "get_type_hints resolves every CandidateEvidence annotation",
          set(row_hints) == set(name for name, _ in _ROW_TYPES),
          "%d resolved" % len(row_hints))
    check("4", "get_type_hints resolves every CarrierSnapshot annotation",
          set(carrier_hints) == set(name for name, _ in _CARRIER_TYPES))

    check("4", "winner_id resolves to int | None",
          record_hints["winner_id"] == (int | None),
          "%r" % (record_hints["winner_id"],))
    check("4", "glancing_displacement resolves to float | None",
          record_hints["glancing_displacement"] == (float | None),
          "%r" % (record_hints["glancing_displacement"],))
    check("4", "carrier resolves to the CarrierSnapshot CLASS",
          record_hints["carrier"] is cr.CarrierSnapshot)
    check("4", "candidates resolves to tuple",
          record_hints["candidates"] is tuple)
    check("4", "disposition resolves to the CandidateDisposition enum",
          row_hints["disposition"] is cr.CandidateDisposition)
    check("4", "slot_eligible resolves to bool | None",
          row_hints["slot_eligible"] == (bool | None))
    check("4", "player_id resolves to int",
          row_hints["player_id"] is int)
    check("4", "dist resolves to float",
          row_hints["dist"] is float)

    mismatched = [
        name for name, declared in _RECORD_TYPES
        if ("None" in declared) != _allows_none(record_hints[name])
    ]
    check("4", "string and resolved type AGREE about None on every record field",
          not mismatched, "mismatched=%s" % mismatched)
    mismatched_rows = [
        name for name, declared in _ROW_TYPES
        if ("None" in declared) != _allows_none(row_hints[name])
    ]
    check("4", "string and resolved type AGREE about None on every row field",
          not mismatched_rows, "mismatched=%s" % mismatched_rows)
    nullable_rows = sorted(name for name, _ in _ROW_TYPES
                           if _allows_none(row_hints[name]))
    info("nullable row fields", "%d: %s" % (len(nullable_rows), nullable_rows))


# ─────────────────────────────────────────────────────────────────────
# [5] THE DERIVED VIEWS
# ─────────────────────────────────────────────────────────────────────

def section_views():
    """The derived readers are properties, so fields() cannot see them."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[5] THE DERIVED VIEWS - properties, invisible to fields()")

    record_field_names = {f.name for f in dataclasses.fields(cr.ContestRecord)}
    row_field_names = {f.name for f in dataclasses.fields(cr.CandidateEvidence)}

    leaked = sorted(set(_RECORD_VIEWS) & record_field_names)
    check("5", "no record reader is a dataclass field", not leaked,
          "leaked=%s" % leaked
          if leaked else "%d readers, 0 fields" % len(_RECORD_VIEWS))
    check("5", "every record reader is a property on the class",
          all(isinstance(getattr(cr.ContestRecord, name, None), property)
              for name in _RECORD_VIEWS),
          "a shape test built on fields() alone would MISS all of them")

    row_for = getattr(cr.ContestRecord, "row_for", None)
    check("5", "row_for is a callable method, not a property",
          callable(row_for) and not isinstance(row_for, property))

    leaked_rows = sorted(set(_ROW_VIEWS) & row_field_names)
    check("5", "no row reader is a dataclass field", not leaked_rows,
          "leaked=%s" % leaked_rows
          if leaked_rows else "%d readers, 0 fields" % len(_ROW_VIEWS))
    check("5", "both row readers are properties on the class",
          all(isinstance(getattr(cr.CandidateEvidence, name, None), property)
              for name in _ROW_VIEWS))

    check("5", "the property list is exactly the declared readers",
          sorted(name for name in dir(cr.ContestRecord)
                 if isinstance(getattr(cr.ContestRecord, name, None),
                               property))
          == sorted(_RECORD_VIEWS),
          "no undocumented property has been added")
    check("5", "the row property list is exactly the declared readers",
          sorted(name for name in dir(cr.CandidateEvidence)
                 if isinstance(getattr(cr.CandidateEvidence, name, None),
                               property))
          == sorted(_ROW_VIEWS))
# ─────────────────────────────────────────────────────────────────────
# THE MOCKED FRAME
# ─────────────────────────────────────────────────────────────────────

class _StubOPE:
    """A minimal open-play engine: scripted verdicts, and it logs the asks."""

    def __init__(self, verdicts):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts)
        self.calls = []
        self.start_calls = []

    def _check_pre_tackle(self, bc, body):
        """Return the scripted verdict for *body*, and record the ask."""
        self.calls.append(getattr(body, "player_id", None))
        return self.verdicts.get(getattr(body, "player_id", None), "MISS")

    def _start_tackle(self, bc, body):
        """Record the TES handover (the mocked frame must not reach TES)."""
        self.start_calls.append(getattr(body, "player_id", None))


def _make_body(player_id, x, y, role="PRIMARY"):
    """A REAL Player carrying the published stamps the authority reads."""
    from player.player import DefenderRole, Player
    body = Player(x=float(x), y=float(y), player_id=int(player_id))
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
    """A REAL carrier one metre short of the mocked line."""
    from player.player import Player
    bc = Player(x=BC_X, y=BC_Y, player_id=1)
    bc.vx = 5.0
    bc.vy = 0.0
    bc.tackle_role = None
    bc.fullback_role_active = False
    return bc


def _make_world(frame, team):
    """The minimal world surface the layer reads."""
    from types import SimpleNamespace
    return SimpleNamespace(
        frame_count=int(frame),
        ball_carrier=None,
        defending_team=list(team),
        tes_active=False,
        ball_owner="BC",
        players=[],
    )


class _MockedFrame:
    """One mocked declaration: the world, the bodies, and the record."""

    def __init__(self, world, bc, line, ope, decision, record, added):
        self.world = world
        self.bc = bc
        self.line = line
        self.ope = ope
        self.decision = decision
        self.record = record
        self.added = added


def _run_mocked_frame():
    """Drive the REAL decider on the mocked proximity line."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    bc = _make_bc()
    line = [_make_body(pid, x, y) for pid, x, y, _verdict in _MOCK_LINE]
    world = _make_world(21, line)
    ope = _StubOPE(_MOCK_VERDICTS)

    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    # Snapshot the world's surface AFTER the resets, so the set measured
    # below is exactly what the DECLARATION added.
    before = set(vars(world))

    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    added = set(vars(world)) - before
    return _MockedFrame(world, bc, line, ope, decision,
                        cp.last_record(world), added)


def _matches(actual, expected) -> bool:
    """Strict comparison: booleans and None compare by IDENTITY.

    ``0 == False`` and ``1 == True`` are both true in Python, so a loose
    comparison would accept a numeric 0 where the leaf declares a bool.
    """
    if isinstance(expected, bool) or expected is None:
        return actual is expected
    return actual == expected


def _conforms(value, annotation) -> bool:
    """True when *value* satisfies the RESOLVED annotation *annotation*."""
    if annotation is type(None):
        return value is None
    args = typing.get_args(annotation)
    if args:
        if value is None:
            return type(None) in args
        return any(_conforms(value, arg) for arg in args
                   if arg is not type(None))
    origin = typing.get_origin(annotation)
    if origin is not None:
        return isinstance(value, origin)
    return isinstance(value, annotation)


# ─────────────────────────────────────────────────────────────────────
# [6] THE MOCKED FRAME — 21 fields, populated
# ─────────────────────────────────────────────────────────────────────

def section_frame():
    """A real declaration on a mocked multi-body line: 21 fields populated."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest as contest_mod
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[6] THE MOCKED FRAME - the record's 21 fields, all populated")

    frame = _run_mocked_frame()
    record = frame.record

    check("6", "the contact radius is the declared 1.5 m",
          ca.contact_radius_m() == CONTACT_RADIUS_M,
          "%s" % ca.contact_radius_m())
    check("6", "the decider FORMED the contest on body 11",
          frame.decision.outcome is ca.ContactOutcome.CONTACT_FORMED
          and frame.decision.winner_id == _BODY_11_WINNER,
          "outcome=%s winner=%s"
          % (frame.decision.outcome.value, frame.decision.winner_id))
    check("6", "the decider SHORT-CIRCUITED at the winner",
          frame.ope.calls == [_BODY_10_REJECTED, _BODY_11_WINNER],
          "predicate calls=%s" % (frame.ope.calls,))
    check("6", "the body inside the radius was NEVER EXAMINED",
          _BODY_12_UNEXAMINED not in frame.ope.calls
          and _BODY_14_OUT_OF_RADIUS not in frame.ope.calls,
          "bodies 12 and 14 were never put to the predicate")
    check("6", "the mocked frame never reached TES",
          frame.ope.start_calls == [],
          "start_calls=%s" % (frame.ope.start_calls,))

    check("6", "last_record(world) published ONE complete record",
          record is not None and cp.last_record(frame.world) is record,
          "None" if record is None else "index=%s" % record.record_index)
    if record is None:
        return None

    check("6", "the record is on the world under its declared attribute",
          getattr(frame.world, cp.CONTEST_RECORD_ATTR, None) is record
          and getattr(frame.world, cp.CONTEST_RECORD_INDEX_ATTR, None)
          == record.record_index,
          "%s / %s" % (cp.CONTEST_RECORD_ATTR, cp.CONTEST_RECORD_INDEX_ATTR))
    check("6", "the declaration added the three declared public attributes",
          {cp.CONTEST_RECORD_ATTR, cp.CONTEST_RECORD_INDEX_ATTR,
           contest_mod.CONTEST_ATTR} <= frame.added,
          "added=%s" % sorted(frame.added))
    # Everything else the declaration adds is the DECIDER's own bookkeeping,
    # which belongs to contact_authority and is not this leaf's to write.
    # What must be EXACT is what the RECORDER writes, so that is measured
    # in isolation below, clear of the authority's own state.
    decider_owned = sorted(frame.added
                           - {cp.CONTEST_RECORD_ATTR,
                              cp.CONTEST_RECORD_INDEX_ATTR,
                              contest_mod.CONTEST_ATTR})
    info("decider-owned attributes", "%d: %s"
         % (len(decider_owned), decider_owned))

    isolated = _make_world(21, frame.line)
    cp.reset_preservation(isolated)
    before_isolated = set(vars(isolated))
    cp.preserve_decider_decision(isolated, frame.bc, frame.line,
                                 frame.decision,
                                 contest_mod.live_contest(frame.world))
    recorder_added = set(vars(isolated)) - before_isolated
    check("6", "the RECORDER's own write is exactly its two attributes",
          recorder_added == {cp.CONTEST_RECORD_ATTR,
                             cp.CONTEST_RECORD_INDEX_ATTR},
          "added=%s" % sorted(recorder_added))
    isolated_record = cp.last_record(isolated)
    check("6", "an ISOLATED recorder run publishes the same declaration",
          isolated_record is not None
          and isolated_record.winner_id == record.winner_id
          and isolated_record.evaluated_ids == record.evaluated_ids
          and isolated_record.in_radius_ids == record.in_radius_ids
          and isolated_record.candidate_count == record.candidate_count,
          "the record is a pure function of the declaration it is handed")

    missing = [name for name, _ in _RECORD_TYPES
               if not hasattr(record, name)]
    check("6", "the record INSTANCE carries all 21 declared fields",
          not missing and len(_RECORD_TYPES) == 21,
          "missing=%s" % missing if missing else "21/21 present")

    for name in sorted(_RECORD_EXPECTED):
        actual = getattr(record, name)
        check("6", "field '%s' is populated" % name,
              _matches(actual, _RECORD_EXPECTED[name]),
              "%r" % (actual,))

    check("6", "field 'candidates' is a populated tuple of the 4 bodies",
          isinstance(record.candidates, tuple)
          and len(record.candidates) == 4,
          "%d rows" % len(record.candidates))
    # The record's carrier is the carrier AS THE AUTHORITY LEFT IT, never a
    # snapshot taken before the frame ran.  Body 10's MISS is a CONTACT-SPACE
    # MISS -- 0.40 m from the carrier, inside the 1.10 m combined body radius --
    # so the CONTACT_SPACE_MISS reaction IS armed for it.  The reaction is a
    # scripted ballistic arc integrated by ``Player.update`` on the FOLLOWING
    # frames, so at declaration time the authority writes no position and no
    # velocity: the published snapshot must equal the live carrier EXACTLY, and
    # the reaction is asserted by the event the decision carries (section [8]).
    # A "> BC_X" clause here would assert a teleport the design forbids.
    check("6", "field 'carrier' is the carrier as the authority left it",
          record.carrier
          == cr.CarrierSnapshot(player_id=1, x=frame.bc.x, y=frame.bc.y,
                                vx=frame.bc.vx, vy=frame.bc.vy,
                                fend_trait=1.0, tackle_break_trait=1.0),
          "%s (live x=%.4f vx=%.4f)"
          % (record.carrier, frame.bc.x, frame.bc.vx))
    # STAGE 3.1 — the placeholders are CARRIED and NEUTRAL.  The mocked bodies
    # are REAL ``Player`` instances, which declare neither trait, so a neutral
    # 1.0 here proves the extractor's default is what reaches the record and
    # that the two new fields cannot scale a later stage's momentum term.
    check("6", "the carrier's trait placeholders are the NEUTRAL multipliers",
          record.carrier.fend_trait == 1.0
          and record.carrier.tackle_break_trait == 1.0,
          "fend=%s break=%s"
          % (record.carrier.fend_trait, record.carrier.tackle_break_trait))
    check("6", "every row's trait placeholders are the NEUTRAL multipliers",
          all(row.tackle_skill_trait == 1.0
              and row.core_strength_trait == 1.0
              for row in record.candidates),
          "%d rows" % len(record.candidates))

    check("6", "record.formed is True for a declared contest",
          record.formed is True)
    check("6", "record.winner resolves to the declared winner's row",
          record.winner is not None
          and record.winner.player_id == _BODY_11_WINNER
          and record.winner.is_winner is True,
          "None" if record.winner is None
          else "winner=%s" % record.winner.player_id)
    check("6", "every derived view is a TUPLE, never a list or a set",
          all(isinstance(view, tuple) for view in
              (record.evaluated, record.in_radius,
               record.un_evaluated_in_radius, record.evaluated_ids,
               record.in_radius_ids, record.un_evaluated_in_radius_ids)))
    check("6", "every count agrees with the length of its own view",
          record.candidate_count == len(record.candidates)
          and record.evaluated_count == len(record.evaluated)
          and record.in_radius_count == len(record.in_radius),
          "candidates=%d evaluated=%d in_radius=%d"
          % (record.candidate_count, record.evaluated_count,
             record.in_radius_count))
    check("6", "the record's radius is the radius OWNER's",
          record.radius_m == ca.contact_radius_m() == CONTACT_RADIUS_M)

    assigned = False
    try:
        record.frame = 999
    except dataclasses.FrozenInstanceError:
        assigned = True
    except Exception:
        assigned = False
    check("6", "the published record REFUSES later mutation", assigned,
          "a consumer cannot rewrite the declaration it was handed")

    check("6", "the candidate views are re-derived from the ROWS",
          record.evaluated
          == tuple(row for row in record.candidates if row.evaluated)
          and record.in_radius
          == tuple(row for row in record.candidates if row.in_radius)
          and record.un_evaluated_in_radius
          == tuple(row for row in record.candidates if row.never_evaluated),
          "the views cannot disagree with the rows they come from")
    check("6", "the id views are the rows' own ids, in row order",
          record.evaluated_ids
          == tuple(row.player_id for row in record.evaluated)
          and record.in_radius_ids
          == tuple(row.player_id for row in record.in_radius)
          and record.un_evaluated_in_radius_ids
          == tuple(row.player_id for row in record.un_evaluated_in_radius))
    check("6", "row_for resolves a known body and returns None otherwise",
          record.row_for(_BODY_12_UNEXAMINED) is not None
          and record.row_for(_BODY_12_UNEXAMINED).player_id
          == _BODY_12_UNEXAMINED
          and record.row_for(999) is None,
          "row_for(999)=%s" % (record.row_for(999),))

    return frame


# ─────────────────────────────────────────────────────────────────────
# [7] THE MAPPING — what each body resolved to
# ─────────────────────────────────────────────────────────────────────

def section_mapping(frame):
    """Each mocked body's disposition, and the row that was thrown away."""
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[7] THE MAPPING - the closed vocabulary, resolved on real bodies")
    record = frame.record

    for player_id in sorted(_EXPECTED_DISPOSITION):
        row = record.row_for(player_id)
        expected = _EXPECTED_DISPOSITION[player_id]
        check("7", "body %d resolves to %s" % (player_id, expected),
              row is not None and row.disposition.name == expected,
              "None" if row is None
              else "got %s" % row.disposition.name)

    check("7", "every row's disposition is a closed-vocabulary member",
          all(isinstance(row.disposition, cr.CandidateDisposition)
              for row in record.candidates),
          "%d rows checked" % len(record.candidates))

    late = record.row_for(_BODY_12_UNEXAMINED)
    distant = record.row_for(_BODY_14_OUT_OF_RADIUS)

    check("7", "the unexamined row IS the declared in-radius disposition",
          late is not None
          and late.disposition is cr.IN_RADIUS_ONLY_DISPOSITION,
          "identity against IN_RADIUS_ONLY_DISPOSITION")
    check("7", "the out-of-radius row IS the declared out-of-radius one",
          distant is not None
          and distant.disposition is cr.OUT_OF_RADIUS_DISPOSITION,
          "identity against OUT_OF_RADIUS_DISPOSITION")
    check("7", "the two geometric rows are NOT reported as evaluated",
          late is not None and distant is not None
          and not late.evaluated and not distant.evaluated,
          "geometry is not a decision")
    check("7", "only the unexamined row reports never_evaluated",
          late is not None and late.never_evaluated is True
          and sum(1 for row in record.candidates if row.never_evaluated) == 1,
          "exactly one row was lost by the short-circuit")

    check("7", "the winner's disposition is a WINNER disposition",
          record.winner is not None
          and record.winner.disposition in cr.WINNER_DISPOSITIONS,
          "None" if record.winner is None
          else record.winner.disposition.value)
    check("7", "exactly ONE row is flagged as the winner",
          sum(1 for row in record.candidates if row.is_winner) == 1,
          "winner_id=%s" % record.winner_id)
    check("7", "the flagged row IS the record's declared winner",
          record.winner is not None
          and record.winner.player_id == record.winner_id
          == _BODY_11_WINNER)
    check("7", "the winner is in BOTH derived views (the decider path)",
          record.winner_id in record.evaluated_ids
          and record.winner_id in record.in_radius_ids,
          "evaluated=%s in_radius=%s"
          % (record.evaluated_ids, record.in_radius_ids))

    check("7", "the discarded row is EXACTLY the unexamined body",
          record.un_evaluated_in_radius_ids == (_BODY_12_UNEXAMINED,),
          "%s" % (record.un_evaluated_in_radius_ids,))
    check("7", "the discarded row is IN radius and NOT evaluated",
          _BODY_12_UNEXAMINED in record.in_radius_ids
          and _BODY_12_UNEXAMINED not in record.evaluated_ids,
          "the information Stage 1B destroyed at the boundary")
    check("7", "the discarded row is CLOSER than the winner",
          late is not None and record.winner is not None
          and late.dist < record.winner.dist,
          "discarded dist=%.4f < winner dist=%.4f"
          % (late.dist, record.winner.dist))
    info("distances", ", ".join(
        "%s=%.4f" % (row.player_id, row.dist) for row in record.candidates))

    check("7", "the decider's own words survive on the row it rejected",
          record.row_for(_BODY_10_REJECTED) is not None
          and record.row_for(_BODY_10_REJECTED).failed_at == "score_miss"
          and record.row_for(_BODY_10_REJECTED).predicate_verdict == "MISS",
          "failed_at=%s"
          % record.row_for(_BODY_10_REJECTED).failed_at)
    check("7", "the winner's row carries the FORM conjunct",
          record.winner is not None
          and record.winner.failed_at == "formed",
          "None" if record.winner is None else record.winner.failed_at)
    check("7", "the UNEXAMINED row says so HONESTLY: failed_at is None",
          late is not None and late.failed_at is None
          and late.predicate_verdict is None,
          "the decider never described this body")
    check("7", "the out-of-radius row names the radius conjunct",
          distant is not None and distant.failed_at == "out_of_radius",
          "None" if distant is None else distant.failed_at)


# ─────────────────────────────────────────────────────────────────────
# [8] THE ROW MATRIX — 20 fields, and independent recomputation
# ─────────────────────────────────────────────────────────────────────

def section_row_matrix(frame):
    """All 20 row fields, and the facts recomputed from the mocked geometry."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[8] THE ROW MATRIX - 20 fields per body, recomputed independently")
    record = frame.record
    # LIVE positions: the record is built from the frame AS THE AUTHORITY LEFT
    # IT.  This frame contains a contact-space MISS, so the carrier has been
    # nudged forward and the recoiling defender has been pushed back; a row's
    # recorded ``dist`` is the carrier-to-body distance in THAT geometry, not
    # the distance under the undisturbed mock line.
    positions = {body.player_id: (body.x, body.y) for body in frame.line}

    incomplete = []
    for row in record.candidates:
        incomplete += [(row.player_id, name) for name, _ in _ROW_TYPES
                       if not hasattr(row, name)]
    check("8", "every row instance carries all 20 declared fields",
          not incomplete and len(_ROW_TYPES) == 20,
          "missing=%s" % incomplete if incomplete else "20/20 on every row")

    row_hints = typing.get_type_hints(cr.CandidateEvidence)
    unconforming = []
    for row in record.candidates:
        unconforming += [(row.player_id, name) for name, _ in _ROW_TYPES
                         if not _conforms(getattr(row, name),
                                          row_hints[name])]
    check("8", "every row VALUE conforms to its resolved declared type",
          not unconforming,
          "offenders=%s" % unconforming if unconforming
          else "%d values checked" % (len(record.candidates) * 20))

    check("8", "the rows are in POPULATION order, one per examined body",
          tuple(row.player_id for row in record.candidates)
          == tuple(pid for pid, _x, _y, _v in _MOCK_LINE),
          "%s" % (tuple(row.player_id for row in record.candidates),))
    check("8", "the carrier itself is not one of the rows",
          all(row.player_id != frame.bc.player_id
              for row in record.candidates),
          "the carrier is the reference, not a candidate")

    distance_errors = []
    for row in record.candidates:
        x, y = positions[row.player_id]
        expected = math.hypot(frame.bc.x - x, frame.bc.y - y)
        if abs(row.dist - expected) > 1e-12:
            distance_errors.append((row.player_id, row.dist, expected))
    check("8", "every row's dist IS the recomputed carrier distance (live "
             "geometry, and the record encodes the LIVE carrier)",
          not distance_errors,
          "errors=%s" % distance_errors if distance_errors
          else "%d distances recomputed with hypot" % len(record.candidates))

    # ...and the disturbance is ASSERTED, not merely tolerated: exactly one
    # body was inside contact space, and its reaction is what moved the carrier
    # forward and pushed that body back.  A silent change of the reaction would
    # otherwise leave this matrix measuring nothing in particular.
    # The reaction is an arc, so its SIGNATURE is travel, not a sign on one
    # axis: the two arcs point along the pair normal, whose orientation depends
    # on which side the defender is, so the axis-free claims are the ones the
    # design actually makes -- exactly one applied reaction, inside contact
    # space, and a DEFENDER arc strictly larger than the carrier's.
    events = getattr(frame.decision, "contact_space_miss_events", ()) or ()
    event = events[0] if len(events) == 1 else None
    check("8", "the frame's geometry is disturbed by EXACTLY ONE "
             "contact-space MISS reaction, and the DEFENDER takes the "
             "larger arc",
          event is not None and event.applied
          and event.separation_m <= event.combined_radius_m
          and event.defender_travel_m > event.carrier_travel_m > 0.0,
          "events=%s" % (events,))

    radius_errors = [row.player_id for row in record.candidates
                     if row.in_radius != (row.dist < record.radius_m)]
    check("8", "every row's in_radius flag AGREES with the declared geometry",
          not radius_errors,
          "offenders=%s" % radius_errors if radius_errors
          else "dist < %s m, for every row" % record.radius_m)

    flag_errors = [
        row.player_id for row in record.candidates
        if row.evaluated != (row.disposition in cr.EVALUATED_DISPOSITIONS)
        or row.never_evaluated
        != (row.disposition is cr.IN_RADIUS_ONLY_DISPOSITION)
    ]
    check("8", "every row's two flags agree with its own disposition",
          not flag_errors, "offenders=%s" % flag_errors)

    role_holders = sum(1 for row in record.candidates
                       if row.tackle_role in ca.TACKLE_ROLE_INITIATORS)
    check("8", "role_holder_count equals the recomputed role-holder count",
          record.role_holder_count == role_holders,
          "record=%d recomputed=%d"
          % (record.role_holder_count, role_holders))

    check("8", "the population is reported as the CALLER's own list",
          record.population_source == cp.POPULATION_SOURCE_CALLER,
          "%s" % record.population_source)
    check("8", "candidate_count covers every body examined, in frame order",
          record.candidate_count == len(_MOCK_LINE)
          == len(record.candidates),
          "%d bodies" % record.candidate_count)
    check("8", "the declaration is INDEXED once, from zero",
          record.record_index == 1
          and getattr(frame.world, cp.CONTEST_RECORD_INDEX_ATTR) == 1)

    ids = {row.player_id for row in record.candidates}
    check("8", "the rows name EXACTLY the mocked bodies and nothing else",
          ids == {pid for pid, _x, _y, _v in _MOCK_LINE},
          "%s" % sorted(ids))

    check("8", "the out-of-radius row's dist EXCEEDS the declared radius",
          record.row_for(_BODY_14_OUT_OF_RADIUS).dist > record.radius_m,
          "%.4f m vs %.2f m" % (record.row_for(_BODY_14_OUT_OF_RADIUS).dist,
                                record.radius_m))
    check("8", "every in-radius row's dist is BELOW the declared radius",
          all(row.dist < record.radius_m for row in record.in_radius),
          "%d in-radius rows" % len(record.in_radius))


# ─────────────────────────────────────────────────────────────────────
# [9] THE POSITIONAL PROFILE (Stage 4) — the mapper and its live slots
# ─────────────────────────────────────────────────────────────────────
# The leaf above proves the record CARRIES two trait slots and that they read
# the neutral 1.0 fallback on a body that declares nothing.  This section
# proves the other half: the positional mapper FILLS those slots from a body's
# jersey number, and the record then carries the DECLARED profile rather than
# the fallback.  The two are complements — the leaf's neutrality assertions
# stay true because the mapper writes nothing to an unstamped body.

def _run_positional_frame():
    """Drive the REAL decider on a line whose bodies carry positional jerseys.

    Identical to ``_run_mocked_frame`` except every body is stamped through the
    real positional mapper first, so the record the authority publishes carries
    live positional profiles.
    """
    from player import positional_traits as pt
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    # A BACK carries (jersey 3) and every defender is a FORWARD.
    bc = _make_bc()
    bc.jersey_number = 3
    pt.apply_positional_traits(bc)

    jerseys = {
        _BODY_10_REJECTED: 9,
        _BODY_11_WINNER: 11,
        _BODY_12_UNEXAMINED: 12,
        _BODY_14_OUT_OF_RADIUS: 13,
    }
    line = []
    for pid, x, y, _verdict in _MOCK_LINE:
        body = _make_body(pid, x, y)
        body.jersey_number = jerseys[pid]
        pt.apply_positional_traits(body)
        line.append(body)

    world = _make_world(21, line)
    ope = _StubOPE(_MOCK_VERDICTS)
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    return _MockedFrame(world, bc, line, ope, decision,
                        cp.last_record(world), set())


def section_positional_profile():
    """The jersey -> physics mapper, and the live slots it fills on a body."""
    from player import positional_traits as pt
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1 import contest_record as cr

    print("\n[9] THE POSITIONAL PROFILE - the mapper and the live slots it fills")

    # ── THE DECLARED BANDS ──────────────────────────────────────────
    check("9", "the three declared jersey bands are pairwise DISJOINT",
          not (pt.BACK_JERSEYS & pt.FORWARD_JERSEYS)
          and not (pt.BACK_JERSEYS & pt.BENCH_PROP_JERSEYS)
          and not (pt.FORWARD_JERSEYS & pt.BENCH_PROP_JERSEYS),
          "back=%s forward=%s bench=%s"
          % (sorted(pt.BACK_JERSEYS), sorted(pt.FORWARD_JERSEYS),
             sorted(pt.BENCH_PROP_JERSEYS)))
    check("9", "backs are 1-7; forwards are 8-13 plus the bench props 14-17",
          set(pt.BACK_JERSEYS) == set(range(1, 8))
          and set(pt.FORWARD_JERSEYS) == set(range(8, 14))
          and set(pt.BENCH_PROP_JERSEYS) == {14, 15, 16, 17})

    check("9", "the BACK profile is exactly 88.0 kg / fend 1.35 / tackle-skill 0.85",
          (pt.BACK_PROFILE.weight_kg, pt.BACK_PROFILE.fend_trait,
           pt.BACK_PROFILE.tackle_skill_trait) == (88.0, 1.35, 0.85),
          "%s" % (pt.BACK_PROFILE,))
    check("9", "the FORWARD profile is exactly 105.0 kg / fend 1.15 / tackle-skill 1.30",
          (pt.FORWARD_PROFILE.weight_kg, pt.FORWARD_PROFILE.fend_trait,
           pt.FORWARD_PROFILE.tackle_skill_trait) == (105.0, 1.15, 1.30),
          "%s" % (pt.FORWARD_PROFILE,))

    expected = {}
    for jersey in pt.BACK_JERSEYS:
        expected[jersey] = pt.BACK_PROFILE
    for jersey in (pt.FORWARD_JERSEYS | pt.BENCH_PROP_JERSEYS):
        expected[jersey] = pt.FORWARD_PROFILE
    check("9", "the lookup resolves EVERY declared jersey to its own profile",
          all(pt.traits_for_jersey(j) is expected[j] for j in expected),
          "%d jerseys" % len(expected))

    unmapped_ok = True
    for unmapped in (0, -1, 18, 19, 99, 1000, None, "prop", ""):
        if pt.traits_for_jersey(unmapped) is not None:
            unmapped_ok = False
    check("9", "an UNMAPPED jersey reports None rather than an invented profile",
          unmapped_ok,
          "0 / negative / 18+ / None / non-numeric are all unmapped")
    check("9", "is_back_jersey / is_forward_jersey agree with the lookup",
          pt.is_back_jersey(1) and pt.is_back_jersey(7)
          and not pt.is_back_jersey(8)
          and pt.is_forward_jersey(8) and pt.is_forward_jersey(13)
          and pt.is_forward_jersey(17) and not pt.is_forward_jersey(7))

    # ── THE WRITE: exactly the three declared slots, nothing else ───
    bare = _make_body(77, 59.0, 35.0)
    bare.jersey_number = 11
    before = set(vars(bare))
    applied = pt.apply_positional_traits(bare)
    added = set(vars(bare)) - before
    check("9", "the mapper reports SUCCESS on a mapped jersey", applied is True)
    check("9", "the mapper writes EXACTLY the three declared live slots",
          added == {pt.WEIGHT_KG_ATTR, pt.FEND_TRAIT_ATTR,
                    pt.TACKLE_SKILL_TRAIT_ATTR},
          "added=%s" % sorted(added))
    check("9", "the three slots land in the fields the message says they do",
          bare.mass_kg == pt.FORWARD_PROFILE.weight_kg
          and bare.fend_trait == pt.FORWARD_PROFILE.fend_trait
          and bare.tackle_skill_trait == pt.FORWARD_PROFILE.tackle_skill_trait,
          "mass_kg=%s fend=%s skill=%s"
          % (bare.mass_kg, bare.fend_trait, bare.tackle_skill_trait))
    check("9", "the two UNCONSUMED Stage 3.1 slots are left ABSENT",
          not hasattr(bare, "core_strength_trait")
          and not hasattr(bare, "tackle_break_trait"),
          "filling a slot no physics reads would manufacture a measurement")

    # ── THE WIRING: the leaf's own extractors read the stamped values ──
    bc_back = _make_bc()
    check("9", "an UNSTAMPED carrier still reads the NEUTRAL fallback",
          pt.traits_for_jersey(bc_back.jersey_number) is None
          and cr.CarrierSnapshot.of(bc_back).fend_trait == 1.0,
          "jersey=%s fend=%s"
          % (bc_back.jersey_number, cr.CarrierSnapshot.of(bc_back).fend_trait))

    bc_back.jersey_number = 3
    check("9", "a BACK carrier's snapshot carries its declared fend",
          pt.apply_positional_traits(bc_back) is True
          and cr.CarrierSnapshot.of(bc_back).fend_trait
          == pt.BACK_PROFILE.fend_trait,
          "fend=%s" % cr.CarrierSnapshot.of(bc_back).fend_trait)

    bc_fwd = _make_bc()
    bc_fwd.jersey_number = 11
    pt.apply_positional_traits(bc_fwd)
    check("9", "a FORWARD carrier's snapshot carries ITS declared fend",
          cr.CarrierSnapshot.of(bc_fwd).fend_trait == pt.FORWARD_PROFILE.fend_trait
          and cr.CarrierSnapshot.of(bc_fwd).fend_trait
          != cr.CarrierSnapshot.of(bc_back).fend_trait,
          "forward=%s vs back=%s"
          % (cr.CarrierSnapshot.of(bc_fwd).fend_trait,
             cr.CarrierSnapshot.of(bc_back).fend_trait))

    fwd_body = _make_body(78, 59.0, 35.0)
    fwd_body.jersey_number = 13
    pt.apply_positional_traits(fwd_body)
    check("9", "the row builder's own extractor reads a stamped tackle-skill",
          cp._neutral_trait(fwd_body, "tackle_skill_trait")
          == pt.FORWARD_PROFILE.tackle_skill_trait,
          "skill=%s" % cp._neutral_trait(fwd_body, "tackle_skill_trait"))
    check("9", "the row builder's own extractor still reports neutral for an "
          "unstamped body",
          cp._neutral_trait(_make_body(79, 59.0, 35.0), "tackle_skill_trait")
          == 1.0)

    # ── END TO END: the published RECORD carries the declared profiles ──
    frame = _run_positional_frame()
    record = frame.record
    check("9", "the mocked positional frame still declares ONE complete record",
          record is not None and frame.ope.calls
          == [_BODY_10_REJECTED, _BODY_11_WINNER],
          "None" if record is None else "winner=%s" % record.winner_id)
    if record is not None:
        check("9", "the record's carrier carries the STAMPED back fend, not 1.0",
              record.carrier.fend_trait == pt.BACK_PROFILE.fend_trait,
              "fend=%s" % record.carrier.fend_trait)
        check("9", "every ROW carries its body's stamped forward tackle-skill",
              all(row.tackle_skill_trait == pt.FORWARD_PROFILE.tackle_skill_trait
                  for row in record.candidates),
              "skills=%s" % sorted({row.tackle_skill_trait
                                    for row in record.candidates}))
        check("9", "the winner's row is the FORWARD profile, the carrier a BACK",
              record.row_for(_BODY_11_WINNER).tackle_skill_trait == 1.30
              and record.carrier.fend_trait == 1.35,
              "the declared bands reach the record intact")
        check("9", "the row that survived the short-circuit is STILL preserved",
              record.un_evaluated_in_radius_ids == (_BODY_12_UNEXAMINED,),
              "%s" % (record.un_evaluated_in_radius_ids,))

    # ── THE LEVER ──────────────────────────────────────────────────
    saved = os.environ.get(pt.POSITIONAL_TRAITS_ENV_VAR)
    try:
        os.environ[pt.POSITIONAL_TRAITS_ENV_VAR] = "0"
        probe = _make_bc()
        probe.jersey_number = 3
        off_applied = pt.apply_positional_traits(probe)
        off_fend = cr.CarrierSnapshot.of(probe).fend_trait
    finally:
        if saved is None:
            os.environ.pop(pt.POSITIONAL_TRAITS_ENV_VAR, None)
        else:
            os.environ[pt.POSITIONAL_TRAITS_ENV_VAR] = saved

    check("9", "with the lever OFF a MAPPED body is left completely untouched",
          off_applied is False and off_fend == 1.0
          and not hasattr(probe, "mass_kg"),
          "applied=%s fend=%s" % (off_applied, off_fend))
    check("9", "the lever is read at CONSUMPTION time (in-process flip)",
          pt.positional_traits_enabled() is True)

    # ── TOTALITY ───────────────────────────────────────────────────
    class _Refuses:
        jersey_number = 3

        def __setattr__(self, name, value):
            raise RuntimeError("this body refuses every store")

    totality_ok = True
    for case in (object(), _Refuses(), None):
        try:
            if pt.apply_positional_traits(case) is not False:
                totality_ok = False
        except Exception:
            totality_ok = False
    check("9", "the write is TOTAL: no body can make it raise or mis-report",
          totality_ok,
          "a body with no jersey, one that refuses stores, and None all report False")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    """Run every section, write the report and the log, return the exit code."""
    handle = _install_log()
    try:
        print("=" * 80)
        print("CONTEST RECORD SCHEMA CONTRACT VERIFICATION")
        print("(the pure-data leaf: its 21-field record, its 20-field body row,")
        print(" its string annotations, its derived views, and a mocked frame")
        print(" in which the row the authority never reached is PRESERVED)")
        print("=" * 80)

        section_leaf()
        section_vocabulary()
        section_schema()
        section_annotations()
        section_views()
        frame = section_frame()
        if frame is not None:
            section_mapping(frame)
            section_row_matrix(frame)

        section_positional_profile()

        failed = [row for row in RESULTS if not row["passed"]]
        report = {
            "checks": len(RESULTS),
            "failed": len(failed),
            "passed": len(RESULTS) - len(failed),
            "sections": sorted({row["section"] for row in RESULTS}),
            "log_path": LOG_PATH,
            "report_path": REPORT_PATH,
            "results": RESULTS,
        }
        with open(REPORT_PATH, "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2, sort_keys=True)

        print("\n" + "=" * 80)
        print("checks=%d passed=%d failed=%d"
              % (report["checks"], report["passed"], report["failed"]))
        print("log written to %s" % LOG_PATH)
        print("report written to %s" % REPORT_PATH)
        print("=" * 80)
        if failed:
            for row in failed:
                print("  [%s] %s - %s"
                      % (row["section"], row["name"], row["detail"]))
            print("RESULT: FAIL")
            return 1
        print("RESULT: PASS")
        return 0
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        try:
            handle.flush()
            handle.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
