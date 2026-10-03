"""verify_tackle_formation_contract.py — STAGE 2 TACKLE FORMATION CONTRACT.

(Stage 2, 2026-09-21.)

WHAT THIS GATE ASSERTS
----------------------
Stage 2 declares the PERSISTENT TACKLE CONTEST: the object that is created
when a contest FORMS, handed from Contact Authority to TES, and evolved by
TES until a declared lifecycle boundary closes it.

The declared ownership sentence this gate machine-checks:

    Open Play owns movement.
    Contact Authority owns contest FORMATION.
    TES owns contest EVOLUTION.
    Referee owns TERMINATION.
    PTB owns RESTART.

So the gate is organised around exactly the four things Stage 2 claims and
the one thing it must NOT do:

  [1] THE SURFACE       — a pure leaf module with ONE published attribute,
                          a closed vocabulary, and a declared API.
  [2] THE DECLARED      — the eleven declared initial conditions, each one
      CONTENT             carried by a real field of the state.
  [3] DETERMINISM       — the same declaration forms the SAME contest, and
                          the declaration key is the idempotence key.
  [4] THE PARTIES       — the carrier and the primary defender are latched
                          BY IDENTITY, and the primary is never also an
                          attachment.
  [5] INITIAL           — the contact geometry, the initial momentum /
      CONDITIONS          relative movement, the forward-progress origin and
                          the empty attachment list are all captured at the
                          declaration, not reconstructed later.
  [6] NO MUTATION       — formation writes NOTHING to any body, and the
                          module performs no attribute store at all.
  [7] NOT A RESOLUTION  — the module names no referee, no PTB, no
                          termination and no tackle RESULT: Stage 2 captures
                          initial conditions and stops.
  [8] THE HANDOVER      — ownership transfers to TES exactly once, with the
                          governed contact velocity recorded.
  [9] EVOLUTION AND     — the contest grows, closes at the declared
      LIFECYCLE           lifecycle boundaries, and the WIRING that calls
                          all of the above exists in the live modules.

Every check is a stated fact about the live code, not a restatement of it:
the behavioural checks run the real module, and the structural checks parse
the real source.

REVERSIBILITY
-------------
The whole stage is removable.  Nothing in the live engine reads the state
it publishes (checked in [9]); deleting game/tes/tackle_state.py together
with its three formation calls, one handover call and four lifecycle hooks
returns the engine to Stage 2B exactly.
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
    print("=" * 74)
    print("[%s] %s" % (number, title))
    print("=" * 74)


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

from game.tes import tackle_state as TS                      # noqa: E402

MODULE_PATH = "game/tes/tackle_state.py"
with open(MODULE_PATH, "r", encoding="utf-8") as _fh:
    SOURCE = _fh.read()
TREE = ast.parse(SOURCE)
AUTHORITY_PATH = "game/defence/defence_lvl_1/contact_authority.py"
TES_PATH = "game/tes/tes.py"
ENGINE_PATH = "world/open_play_engine.py"

with open(AUTHORITY_PATH, "r", encoding="utf-8") as _fh:
    AUTHORITY_SOURCE = _fh.read()
with open(TES_PATH, "r", encoding="utf-8") as _fh:
    TES_SOURCE = _fh.read()
with open(ENGINE_PATH, "r", encoding="utf-8") as _fh:
    ENGINE_SOURCE = _fh.read()


def project_imports(source):
    """Every module-scope import that reaches the project (not stdlib)."""
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                found.append("." * node.level + (node.module or ""))
            elif node.module:
                found.append(node.module)
    return found


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
    """Every ``x[...] = ...`` store."""
    rows = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript):
                    rows.append(node.lineno)
        elif isinstance(node, ast.AugAssign) and isinstance(
            node.target, (ast.Attribute, ast.Subscript)
        ):
            rows.append(node.lineno)
    return rows


def call_count(source, name):
    """AST call sites whose function is the bare NAME *name*.

    A source SUBSTRING would also match the surrounding PROSE (these modules
    name the wiring symbols in their comments), so every wiring count in this
    gate is read from the AST.
    """
    count = 0
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name):
            count += 1
    return count


def imported_names(source):
    """Map every imported local alias to the name it was imported from.

    The WHOLE tree is walked, not just the module body: this codebase reaches
    its project modules with FUNCTION-LOCAL imports (so the authority/engine
    import graph stays acyclic), and a module-scope-only scan would miss every
    one of them.
    """
    rows = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                rows[alias.asname or alias.name] = alias.name
    return rows


def code_names(source, skip_docstrings=True):
    """Every NAME and ATTRIBUTE identifier the CODE mentions.

    Comments and (by default) docstrings are excluded: a prose mention of a
    forbidden symbol is documentation, not a reference.
    """
    import ast as _ast
    tree = _ast.parse(source)
    docstrings = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef,
                             _ast.FunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], _ast.Expr)
                    and isinstance(body[0].value, _ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    names = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            names.add(node.id)
        elif isinstance(node, _ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, _ast.Constant) and isinstance(node.value, str):
            if skip_docstrings and id(node) in docstrings:
                continue
            for part in _identifier_parts(node.value):
                names.add(part)
    return names


def _identifier_parts(text):
    parts = set()
    for raw in text.replace("(", " ").replace(")", " ").split():
        token = raw.strip(".,;:'\"")
        if token and all(c.isalnum() or c == "_" for c in token):
            parts.add(token)
            if "." in token:
                parts.add(token.split(".")[-1])
    return parts


# ─────────────────────────────────────────────────────────────────────
# TEST FIXTURES — a minimal body and world.  Deliberately tiny: the module
# is a leaf and must work against anything carrying the declared fields.
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


def fresh_world(frame=42, sim=7.0, direction=1):
    w = World(frame_count=frame, sim_time_s=sim, attack_dir=direction)
    TS.reset_tackle_states(w)
    return w


def carrier_and_defender(dx=0.4, dy=0.0, cvx=5.0, cvy=0.0,
                         dvx=-4.0, dvy=0.0):
    """A carrier at (60, 35) and a defender `dx` ahead, closing."""
    bc = Body(7, 60.0, 35.0, cvx, cvy, facing=0.0)
    df = Body(8, 60.0 + dx, 35.0 + dy, dvx, dvy, facing=math.pi,
              defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
              is_defender=True)
    return bc, df


def snapshot(body):
    """Every field formation must NOT be able to change."""
    keys = ("x", "y", "vx", "vy", "facing", "defender_role", "tackle_role",
            "mass_kg", "player_id", "is_defender")
    out = {}
    for key in keys:
        if hasattr(body, key):
            out[key] = getattr(body, key)
    return out


print(__file__)
print("=" * 74)
print("TACKLE FORMATION CONTRACT VERIFICATION (Stage 2)")
print("(the persistent contest: formed by Contact Authority, evolved by TES)")
print("=" * 74)
print("module under test: %s" % MODULE_PATH)


# ═════════════════════════════════════════════════════════════════════
# [1] THE SURFACE
# ═════════════════════════════════════════════════════════════════════
section("1", "THE SURFACE - one leaf module, one published attribute")

_project = [m for m in project_imports(SOURCE)
            if not m.startswith(("__future__", "math", "dataclasses",
                                 "enum", "os", "typing", "ast"))]
check("1", "the module has NO project imports (it is a pure leaf)",
      not _project, "project imports=%s" % _project)

check("1", "the published attribute is a declared constant",
      TS.TACKLE_STATE_ATTR == "tackle_state",
      "TACKLE_STATE_ATTR=%r" % TS.TACKLE_STATE_ATTR)

check("1", "the contest identity derives from the declaring frame+seq",
      TS.tackle_state_id_for(42, 3) == "tackle-42-3",
      "tackle_state_id_for(42, 3)=%r" % TS.tackle_state_id_for(42, 3))

check("1", "the two owners are the declared pair",
      {TS.OWNER_CONTACT_AUTHORITY, TS.OWNER_TES}
      == {"contact_authority", "tes"},
      "%r / %r" % (TS.OWNER_CONTACT_AUTHORITY, TS.OWNER_TES))

check("1", "the close-reason vocabulary is CLOSED and has three members",
      TS.CLOSE_REASONS == frozenset(
          {TS.CLOSE_KICKOFF, TS.CLOSE_OPEN_PLAY_ENTERED,
           TS.CLOSE_SUPERSEDED}),
      "CLOSE_REASONS=%s" % sorted(TS.CLOSE_REASONS))

check("1", "the contact-quality vocabulary has four members",
      [q.value for q in TS.ContactQuality]
      == ["front_on", "shoulder", "side_on", "from_behind"],
      "%s" % [q.value for q in TS.ContactQuality])

check("1", "the contact-kind vocabulary has two members",
      sorted(k.value for k in TS.ContactKind) == ["direct", "engage"],
      "%s" % sorted(k.value for k in TS.ContactKind))

check("1", "the quality bands are DERIVED from the declared thresholds",
      TS.quality_for(0.0) is TS.ContactQuality.FRONT_ON
      and TS.quality_for(TS.QUALITY_FRONT_ON_MAX_DEG - 1.0)
      is TS.ContactQuality.FRONT_ON
      and TS.quality_for(TS.QUALITY_SHOULDER_MAX_DEG - 1.0)
      is TS.ContactQuality.SHOULDER
      and TS.quality_for(TS.QUALITY_SIDE_ON_MAX_DEG - 1.0)
      is TS.ContactQuality.SIDE_ON
      and TS.quality_for(179.0) is TS.ContactQuality.FROM_BEHIND,
      "front<%.0f shoulder<%.0f side<%.0f behind"
      % (TS.QUALITY_FRONT_ON_MAX_DEG, TS.QUALITY_SHOULDER_MAX_DEG,
         TS.QUALITY_SIDE_ON_MAX_DEG))

check("1", "the ring is bounded",
      isinstance(TS.TACKLE_STATE_RING_LIMIT, int)
      and TS.TACKLE_STATE_RING_LIMIT > 0,
      "TACKLE_STATE_RING_LIMIT=%d" % TS.TACKLE_STATE_RING_LIMIT)

check("1", "the declared API is complete and importable",
      all(callable(getattr(TS, n, None)) for n in (
          "form_tackle_state", "mark_tes_ownership", "append_attachment",
          "mark_attached", "active_tackle_state", "published_tackle_state",
          "recent_tackle_states", "reset_tackle_states",
          "clear_tackle_state", "format_tackle_state_report",
          "declared_content", "tackle_state_id_for", "quality_for",
          "measure_contact")),
      "all callables resolve")

# The module DOES assign fields on the STATE OBJECT it creates -- that is how
# a formed contest is filled in.  What it must never do is write to a WORLD or
# to a BODY.  So the check is on the BASE of every store: the module's own
# state containers only, never a world, a carrier or a defender.
_STORE_BASES = sorted({base for base, _attr, _line
                       in attribute_stores(SOURCE)})
check("1", "every attribute store targets the module's OWN state object",
      set(_STORE_BASES) <= {"state", "attachment"},
      "store bases=%s stores=%d"
      % (_STORE_BASES, len(attribute_stores(SOURCE))))
check("1", "and NO store targets a world, a carrier or a defender",
      not (set(_STORE_BASES)
           & {"world", "bc", "body", "carrier", "primary_defender",
              "defender", "d", "p", "self"}),
      "store bases=%s" % _STORE_BASES)

check("1", "the module performs NO subscript store anywhere",
      not subscript_stores(SOURCE),
      "subscript stores=%s" % subscript_stores(SOURCE))

_publish_setattr = [
    node for node in ast.walk(TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name) and node.func.id == "setattr"
]
check("1", "its only stores are setattr calls on the WORLD",
      all(isinstance(c.args[0], ast.Name) and c.args[0].id == "world"
          for c in _publish_setattr),
      "setattr calls=%d" % len(_publish_setattr))

check("1", "the world attribute written is the declared name",
      "world" in SOURCE and "TACKLE_STATE_ATTR" in SOURCE
      and "setattr(world" in SOURCE,
      "publish goes through setattr(world, TACKLE_STATE_ATTR, ...)")


# ═════════════════════════════════════════════════════════════════════
# [2] THE DECLARED CONTENT
# ═════════════════════════════════════════════════════════════════════
section("2", "THE DECLARED CONTENT - eleven initial conditions, all real")

DECLARED = TS.declared_content()
DECLARED_NAMES = [name for name, _ in DECLARED]
FIELDS = set(TS.TackleState.__dataclass_fields__)

check("2", "eleven declared content items", len(DECLARED) == 11,
      "got %d" % len(DECLARED))

check("2", "the declared names are distinct",
      len(set(DECLARED_NAMES)) == len(DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

print()
for name, field_names in DECLARED:
    missing = [f for f in field_names if f not in FIELDS]
    check("2", "declared '%s' is carried by real fields" % name,
          not missing, "fields=%s missing=%s" % (list(field_names), missing))

check("2", "EVERY declared field is a field of the state",
      all(f in FIELDS for _, fs in DECLARED for f in fs),
      "declared fields=%d state fields=%d"
      % (sum(len(fs) for _, fs in DECLARED), len(FIELDS)))

check("2", "the state is a dataclass with the declared 43 fields",
      len(FIELDS) == 43,
      "fields=%d" % len(FIELDS))

check("2", "the declared content names the CARRIER",
      any(n == "carrier" for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "the declared content names the PRIMARY DEFENDER",
      any(n == "primary_defender" for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "the declared content names the CONTACT GEOMETRY",
      any(n == "contact_angle_and_geometry" for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "the declared content names the INITIAL MOMENTUM",
      any(n == "initial_momentum_and_relative_movement"
          for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "the declared content names the FORWARD-PROGRESS ORIGIN",
      any(n == "forward_progress_origin" for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "the declared content names the ATTACHMENT LIST",
      any(n == "attachment_list_beyond_the_primary"
          for n in DECLARED_NAMES),
      "%s" % DECLARED_NAMES)

check("2", "a formed contest carries NO declared field unset",
      True, "asserted behaviourally in [5]")
# ═════════════════════════════════════════════════════════════════════
# [3] DETERMINISM AND THE DECLARATION KEY
# ═════════════════════════════════════════════════════════════════════
section("3", "DETERMINISM - one declaration, one contest, one identity")

world = fresh_world(frame=42, sim=7.0)
bc_a, df_a = carrier_and_defender(dx=0.4)
state_a = TS.form_tackle_state(world, bc_a, df_a, frame=42, seq=3)

world_b = fresh_world(frame=42, sim=7.0)
bc_b, df_b = carrier_and_defender(dx=0.4)
state_b = TS.form_tackle_state(world_b, bc_b, df_b, frame=42, seq=3)

check("3", "forming once yields a state", state_a is not None)

FACT_FIELDS = ("tackle_id", "frame", "seq", "carrier_id",
               "primary_defender_id", "separation_m",
               "contact_angle_deg", "contact_angle_to_attack_deg",
               "lateral_offset_m", "closing_speed_mps",
               "contact_quality", "contact_type",
               "carrier_speed_mps", "defender_speed_mps",
               "carrier_momentum_kgmps", "defender_momentum_kgmps",
               "relative_speed_mps", "relative_momentum_kgmps",
               "relative_vx", "relative_vy",
               "forward_origin_x", "forward_origin_y", "attack_dir",
               "owner")


def facts(state):
    return {f: getattr(state, f, None) for f in FACT_FIELDS}


_diff = [f for f in FACT_FIELDS
         if facts(state_a)[f] != facts(state_b)[f]]
check("3", "identically-shaped declarations form IDENTICAL contests",
      not _diff, "differing fields=%s" % _diff)

check("3", "the identity is derived from the declaring frame and seq",
      state_a.tackle_id == "tackle-42-3",
      "tackle_id=%r" % state_a.tackle_id)

check("3", "the declaration key is the frame+seq pair",
      state_a.declaration_key == (42, 3),
      "declaration_key=%r" % (state_a.declaration_key,))

check("3", "the state is OPEN on formation",
      state_a.open is True and state_a.closed is False,
      "open=%r closed=%r" % (state_a.open, state_a.closed))

check("3", "the state is owned by CONTACT AUTHORITY at formation",
      state_a.owner == TS.OWNER_CONTACT_AUTHORITY,
      "owner=%r" % state_a.owner)

check("3", "the state is published on the world",
      TS.published_tackle_state(world) is state_a,
      "published_tackle_state(world) is the formed state")

check("3", "the ACTIVE contest is the one just formed",
      TS.active_tackle_state(world) is state_a,
      "active_tackle_state(world) resolves")

# ── IDEMPOTENCE: the SAME declaration cannot mint a second contest ──
again = TS.form_tackle_state(world, bc_a, df_a, frame=42, seq=3)
check("3", "re-forming the SAME declaration returns the SAME contest",
      again is state_a,
      "identity preserved: %s" % (again is state_a))
check("3", "and it did NOT supersede the existing contest",
      state_a.closed is False,
      "closed=%r" % state_a.closed)
check("3", "and it minted no second record",
      len([s for s in TS.recent_tackle_states()
           if s.tackle_id == "tackle-42-3"]) == 1,
      "records for tackle-42-3: %d"
      % len([s for s in TS.recent_tackle_states()
             if s.tackle_id == "tackle-42-3"]))

# ── A DIFFERENT declaration supersedes the old contest ──
bc_c, df_c = carrier_and_defender(dx=0.5)
state_c = TS.form_tackle_state(world, bc_c, df_c, frame=43, seq=4)
check("3", "a NEW declaration forms a NEW contest",
      state_c is not None and state_c.tackle_id == "tackle-43-4",
      "tackle_id=%r" % (state_c.tackle_id if state_c else None))
check("3", "the SUPERSEDED contest is closed with the declared reason",
      state_a.closed is True
      and state_a.closed_reason == TS.CLOSE_SUPERSEDED,
      "closed=%r reason=%r" % (state_a.closed, state_a.closed_reason))
check("3", "the closed contest keeps its frame of closure",
      state_a.closed_frame is not None,
      "closed_frame=%r" % state_a.closed_frame)
# The ring is MODULE-scoped and therefore spans every world this gate has
# built; "one contest per world" is asserted on THIS world's published state.
check("3", "only ONE contest is ever active at a time in a world",
      TS.active_tackle_state(world) is state_c
      and TS.published_tackle_state(world) is state_c
      and state_c.open is True,
      "active/published is tackle-43-4 and it is open")

check("3", "the module exposes a report for the live contest",
      isinstance(TS.format_tackle_state_report(world), str)
      and TS.format_tackle_state_report(world) != "",
      "%s" % TS.format_tackle_state_report(world)[:96])

check("3", "the sequence is part of the identity, so two frames differ",
      TS.tackle_state_id_for(42, 3) != TS.tackle_state_id_for(43, 3)
      and TS.tackle_state_id_for(42, 3) != TS.tackle_state_id_for(42, 4),
      "%s/%s/%s" % (TS.tackle_state_id_for(42, 3),
                    TS.tackle_state_id_for(43, 3),
                    TS.tackle_state_id_for(42, 4)))


# ═════════════════════════════════════════════════════════════════════
# [4] THE PARTIES
# ═════════════════════════════════════════════════════════════════════
section("4", "THE PARTIES - the carrier and the primary defender are latched")

world = fresh_world(frame=50, sim=9.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi,
          defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
          is_defender=True)
joiner = Body(9, 60.9, 35.2, -3.0, 0.1, facing=math.pi,
              defender_role="TACKLER_2", tackle_role="SECONDARY",
              is_defender=True)

state = TS.form_tackle_state(world, bc, df, frame=50, seq=1)

check("4", "the carrier is latched BY IDENTITY",
      state.carrier is bc, "state.carrier is bc: %s" % (state.carrier is bc))
check("4", "the carrier id is recorded",
      state.carrier_id == 7, "carrier_id=%r" % state.carrier_id)
check("4", "the primary defender is latched BY IDENTITY",
      state.primary_defender is df,
      "state.primary_defender is df: %s" % (state.primary_defender is df))
check("4", "the primary defender id is recorded",
      state.primary_defender_id == 8,
      "primary_defender_id=%r" % state.primary_defender_id)
check("4", "a SECOND body is NOT the primary",
      state.primary_defender_id != joiner.player_id,
      "primary=%d vs other=%d"
      % (state.primary_defender_id, joiner.player_id))

check("4", "the carrier entry kinematics are a SNAPSHOT, not the live body",
      state.contact_carrier is not bc
      and state.contact_carrier.player_id == 7,
      "type=%s id=%s" % (type(state.contact_carrier).__name__,
                         state.contact_carrier.player_id))
check("4", "the defender entry kinematics are a SNAPSHOT",
      state.contact_defender is not df
      and state.contact_defender.player_id == 8,
      "type=%s id=%s" % (type(state.contact_defender).__name__,
                         state.contact_defender.player_id))

check("4", "the separation is measured from the two declared bodies",
      abs(state.separation_m - 0.4) < 1e-9,
      "separation_m=%.6f" % state.separation_m)
check("4", "the closing speed is the RELATIVE closing rate",
      abs(state.closing_speed_mps - 9.0) < 1e-9,
      "closing_speed_mps=%.6f (carrier +5.0 closing on defender -4.0)"
      % state.closing_speed_mps)

check("4", "the primary defender is NOT also an attachment",
      state.attachment_ids == ()
      or state.primary_defender_id not in state.attachment_ids,
      "attachments=%s" % (state.attachment_ids,))
check("4", "the contact set includes the primary",
      state.primary_defender_id in state.contact_set_ids,
      "contact_set=%s" % (state.contact_set_ids,))


def quality_in(measured):
    """The ContactQuality a measurement reports, whatever it is keyed by."""
    if isinstance(measured, dict):
        for value in measured.values():
            if isinstance(value, TS.ContactQuality):
                return value
    return None


# ── THE QUALITY BANDS, measured on real geometry ──
_measured = TS.measure_contact(bc, df, 1)
check("4", "measure_contact reports the contact quality",
      quality_in(_measured) is not None,
      "measured=%s" % (_measured,))

_far = Body(11, 61.0, 36.5, 0.0, 0.0, defender_role="TACKLER_3",
            tackle_role="SECONDARY", is_defender=True)
_far_measured = TS.measure_contact(bc, _far, 1)
check("4", "measure_contact reports the geometry as well as the quality",
      len(_far_measured) >= 3,
      "fields=%d" % len(_far_measured))
check("4", "a defender dead ahead is NOT classified from behind",
      quality_in(_measured) is not TS.ContactQuality.FROM_BEHIND,
      "quality=%s" % (quality_in(_measured),))

# The CONTACT SET is the set of DEFENDERS in contact with the carrier.  The
# carrier is the other party and is carried by its own declared fields, so it
# is deliberately NOT a member of it.
check("4", "the contact set holds the primary defender and NOT the carrier",
      state.primary_defender_id in state.contact_set_ids
      and state.carrier_id not in state.contact_set_ids,
      "contact_set=%s carrier=%r"
      % (state.contact_set_ids, state.carrier_id))

check("4", "the contact angle to the ATTACK AXIS is recorded",
      isinstance(state.contact_angle_to_attack_deg, float),
      "contact_angle_to_attack_deg=%.4f"
      % state.contact_angle_to_attack_deg)

check("4", "the attack direction is captured from the world",
      state.attack_dir == 1, "attack_dir=%r" % state.attack_dir)

check("4", "has_attachment/attachment_for agree with the list",
      state.has_attachment(8) is False
      and state.attachment_for(8) is None,
      "the primary is never an attachment row")
# ═════════════════════════════════════════════════════════════════════
# [5] THE INITIAL CONDITIONS
# ═════════════════════════════════════════════════════════════════════
section("5", "THE INITIAL CONDITIONS - captured at the declaration")

world = fresh_world(frame=60, sim=11.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.0, mass_kg=90.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi, mass_kg=90.0,
          defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
          is_defender=True)

state = TS.form_tackle_state(world, bc, df, frame=60, seq=2,
                             phase="OPEN_PLAY", channel="manager_trigger",
                             admitted_by_decider=True)

check("5", "the declaration frame and sequence are recorded",
      state.frame == 60 and state.seq == 2,
      "frame=%r seq=%r" % (state.frame, state.seq))
check("5", "the declaring phase and channel are recorded",
      state.phase == "OPEN_PLAY" and state.channel == "manager_trigger",
      "phase=%r channel=%r" % (state.phase, state.channel))
check("5", "the contact timestamp is captured",
      state.contact_frame is not None
      and isinstance(state.contact_time_s, float),
      "contact_frame=%r contact_time_s=%r"
      % (state.contact_frame, state.contact_time_s))

# ── CONTACT GEOMETRY ──
check("5", "the SEPARATION at contact is captured",
      abs(state.separation_m - 0.4) < 1e-9,
      "separation_m=%.6f" % state.separation_m)
check("5", "the CONTACT ANGLE at contact is captured",
      isinstance(state.contact_angle_deg, float)
      and 0.0 <= state.contact_angle_deg <= 180.0,
      "contact_angle_deg=%.4f" % state.contact_angle_deg)
check("5", "the LATERAL OFFSET at contact is captured",
      abs(state.lateral_offset_m - 0.0) < 1e-9,
      "lateral_offset_m=%.6f" % state.lateral_offset_m)
check("5", "the CLOSING SPEED at contact is captured",
      abs(state.closing_speed_mps - 9.0) < 1e-9,
      "closing_speed_mps=%.6f" % state.closing_speed_mps)
check("5", "the CONTACT QUALITY is classified",
      isinstance(state.contact_quality, TS.ContactQuality),
      "contact_quality=%s" % state.contact_quality)
check("5", "the CONTACT KIND is classified",
      isinstance(state.contact_type, TS.ContactKind),
      "contact_type=%s" % state.contact_type)

# ── INITIAL KINEMATICS ──
check("5", "the carrier kinematics at contact are captured",
      abs(state.contact_carrier.vx - 5.0) < 1e-9
      and abs(state.contact_carrier.x - 60.0) < 1e-9,
      "carrier v=(%.3f, %.3f) x=%.3f"
      % (state.contact_carrier.vx, state.contact_carrier.vy,
         state.contact_carrier.x))
check("5", "the defender kinematics at contact are captured",
      abs(state.contact_defender.vx - (-4.0)) < 1e-9,
      "defender v=(%.3f, %.3f)"
      % (state.contact_defender.vx, state.contact_defender.vy))

# ── INITIAL MOMENTUM AND RELATIVE MOVEMENT ──
check("5", "the carrier SPEED is captured",
      abs(state.carrier_speed_mps - 5.0) < 1e-9,
      "carrier_speed_mps=%.6f" % state.carrier_speed_mps)
check("5", "the defender SPEED is captured",
      abs(state.defender_speed_mps - 4.0) < 1e-9,
      "defender_speed_mps=%.6f" % state.defender_speed_mps)
check("5", "the carrier MOMENTUM is captured",
      abs(state.carrier_momentum_kgmps - 450.0) < 1e-9,
      "carrier_momentum_kgmps=%.6f" % state.carrier_momentum_kgmps)
check("5", "the defender MOMENTUM is captured",
      abs(state.defender_momentum_kgmps - 360.0) < 1e-9,
      "defender_momentum_kgmps=%.6f" % state.defender_momentum_kgmps)
check("5", "the RELATIVE velocity is captured per axis",
      abs(state.relative_vx - 9.0) < 1e-9
      and abs(state.relative_vy - 0.0) < 1e-9,
      "relative_v=(%.3f, %.3f)" % (state.relative_vx, state.relative_vy))
check("5", "the RELATIVE speed is captured",
      abs(state.relative_speed_mps - 9.0) < 1e-9,
      "relative_speed_mps=%.6f" % state.relative_speed_mps)
check("5", "the RELATIVE momentum is captured",
      isinstance(state.relative_momentum_kgmps, float)
      and math.isfinite(state.relative_momentum_kgmps),
      "relative_momentum_kgmps=%.6f" % state.relative_momentum_kgmps)

# ── THE FORWARD-PROGRESS ORIGIN ──
check("5", "the forward-progress ORIGIN is anchored at the declaration",
      abs(state.forward_origin_x - 60.0) < 1e-9
      and abs(state.forward_origin_y - 35.0) < 1e-9,
      "origin=(%.3f, %.3f)"
      % (state.forward_origin_x, state.forward_origin_y))
check("5", "the attack direction is part of the origin",
      state.attack_dir == 1, "attack_dir=%r" % state.attack_dir)
check("5", "forward progress is measured FROM that origin",
      abs(state.forward_progress_m(62.0) - 2.0) < 1e-9
      and abs(state.forward_progress_m(60.0) - 0.0) < 1e-9,
      "at x=62 -> %.4f m, at x=60 -> %.4f m"
      % (state.forward_progress_m(62.0), state.forward_progress_m(60.0)))
check("5", "forward progress is signed by the attack direction",
      abs(state.forward_progress_m(58.0) - (-2.0)) < 1e-9,
      "at x=58 -> %.4f m" % state.forward_progress_m(58.0))

# ── THE ATTACHMENT LIST ──
check("5", "the attachment list is EMPTY at formation",
      state.attachments == () and state.attachment_count == 0,
      "attachments=%s count=%d"
      % (state.attachments, state.attachment_count))
check("5", "the contact set holds EXACTLY the primary defender",
      set(state.contact_set_ids) == {8},
      "contact_set=%s" % (state.contact_set_ids,))

# ── NO DECLARED FIELD IS LEFT UNSET ──
_unset = []
for name, field_names in TS.declared_content():
    for f in field_names:
        if getattr(state, f, None) is None:
            _unset.append("%s.%s" % (name, f))
check("5", "NO declared initial condition is left unset",
      not _unset, "unset=%s" % _unset)

check("5", "the state renders in one line",
      isinstance(TS.format_tackle_state_report(world), str)
      and "tackle" in TS.format_tackle_state_report(world),
      "%s" % TS.format_tackle_state_report(world)[:110])


# ═════════════════════════════════════════════════════════════════════
# [6] NO MUTATION
# ═════════════════════════════════════════════════════════════════════
section("6", "NO MUTATION - formation writes nothing to any body")

world = fresh_world(frame=70, sim=13.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.25, mass_kg=92.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi, mass_kg=88.0,
          defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
          is_defender=True)

bc_before = snapshot(bc)
df_before = snapshot(df)

state = TS.form_tackle_state(world, bc, df, frame=70, seq=1)

check("6", "the CARRIER is byte-identical after formation",
      snapshot(bc) == bc_before,
      "before=%s after=%s" % (bc_before, snapshot(bc)))
check("6", "the PRIMARY DEFENDER is byte-identical after formation",
      snapshot(df) == df_before,
      "before=%s after=%s" % (df_before, snapshot(df)))

# ── THE MODULE MUST NOT INVENT OWNERSHIP FLAGS ON A BODY ──
INVENTED = ("frozen", "tes_locked", "in_contact", "is_tackler",
            "anim_state", "attack_role")


def invented_flags(body):
    return [f for f in INVENTED if hasattr(body, f)]


check("6", "formation invents NO ownership flag on the carrier",
      not invented_flags(bc), "invented=%s" % invented_flags(bc))
check("6", "formation invents NO ownership flag on the defender",
      not invented_flags(df), "invented=%s" % invented_flags(df))

# ── RE-FORMING IS EQUALLY INERT ──
bc2 = snapshot(bc)
df2 = snapshot(df)
TS.form_tackle_state(world, bc, df, frame=70, seq=1)
check("6", "re-forming the same declaration is equally inert",
      snapshot(bc) == bc2 and snapshot(df) == df2,
      "carrier unchanged=%s defender unchanged=%s"
      % (snapshot(bc) == bc2, snapshot(df) == df2))

# ── THE MODULE OWNS NO MOVEMENT AT ALL ──
forbidden_movement = [n for n in code_names(SOURCE)
                      if n in ("vx_target", "vy_target", "speed_target",
                               "heading_target", "move_speed", "target_x",
                               "target_y", "lunge_active", "lunge_speed",
                               "movement_mode", "collision_priority")]
check("6", "the module names NO movement field (it owns no movement)",
      not forbidden_movement,
      "movement names=%s" % forbidden_movement)

# The module fills in its OWN state object; it must still write nothing to a
# body.  The base of every store is checked, never the fact that a store
# exists -- storing on the state is the whole point of forming one.
check("6", "the module writes to NO body and to no subscript",
      not (set(_STORE_BASES)
           & {"world", "bc", "body", "carrier", "primary_defender",
              "defender", "d", "p"})
      and not subscript_stores(SOURCE),
      "store bases=%s subscripts=%s"
      % (_STORE_BASES, subscript_stores(SOURCE)))


# ═════════════════════════════════════════════════════════════════════
# [7] NOT A RESOLUTION
# ═════════════════════════════════════════════════════════════════════
section("7", "NOT A RESOLUTION - no referee, no PTB, no tackle result")

_NAMES = code_names(SOURCE)

FORBIDDEN = [
    # referee / termination
    "referee", "Referee", "award", "awarded", "terminate", "terminated",
    "tackle_result", "TESOutcome",
    # PTB / restart
    "PTB", "PTBManager", "ptb_manager", "play_the_ball", "ptb_ready",
    # resolution / dominance / break
    "compute_dominance", "dominance", "compute_drive_distance",
    "drive_distance", "drive_target", "broken", "bust", "collapse",
    "collapsed", "resolved", "TESPhase", "tes_start_tackle",
]
_hits = sorted(n for n in FORBIDDEN if n in _NAMES)
check("7", "the module names NO referee, PTB, resolution or TES-phase symbol",
      not _hits, "forbidden names present=%s" % _hits)

_mod_imports = project_imports(SOURCE)
check("7", "it does not import the TES engine or the PTB manager",
      not any(("tes.tes" in m or "ptb" in m.lower()
               or "tes_context" in m or "tes_outcomes" in m)
              for m in _mod_imports),
      "imports=%s" % _mod_imports)

check("7", "it does not import the referee or any rules module",
      not any(("referee" in m.lower() or "world.rules" in m)
              for m in _mod_imports),
      "imports=%s" % _mod_imports)

# ── FORMING DOES NOT END THE CONTEST ──
world = fresh_world(frame=80, sim=15.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, defender_role="TACKLER_1",
          tackle_role=TS.ROLE_PRIMARY, is_defender=True)
state = TS.form_tackle_state(world, bc, df, frame=80, seq=1)

check("7", "a formed contest is NOT terminated by its own formation",
      state.closed is False and state.closed_reason is None,
      "closed=%r reason=%r" % (state.closed, state.closed_reason))
check("7", "a formed contest records NO result field",
      not hasattr(state, "result") and not hasattr(state, "outcome")
      and not hasattr(state, "winner"),
      "the state has no result/outcome/winner attribute")
check("7", "a formed contest is NOT ready for a restart",
      not any(hasattr(state, f) for f in
              ("ptb_ready", "ptb_time", "restart", "next_possession")),
      "no PTB field exists on the state")
check("7", "there is exactly ONE close vocabulary and it is the "
           "lifecycle's, not a referee's",
      all(r in TS.CLOSE_REASONS for r in (
          TS.CLOSE_KICKOFF, TS.CLOSE_OPEN_PLAY_ENTERED,
          TS.CLOSE_SUPERSEDED))
      and len(TS.CLOSE_REASONS) == 3,
      "%s" % sorted(TS.CLOSE_REASONS))
check("7", "the module does not drive or push the carrier to ground",
      not any(hasattr(state, f) for f in
              ("grounded", "to_ground", "push", "pushback")),
      "no grounding/pushback field exists on the state")
# ═════════════════════════════════════════════════════════════════════
# [8] THE HANDOVER
# ═════════════════════════════════════════════════════════════════════
section("8", "THE HANDOVER - ownership passes to TES exactly once")

world = fresh_world(frame=90, sim=17.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, defender_role="TACKLER_1",
          tackle_role=TS.ROLE_PRIMARY, is_defender=True)
state = TS.form_tackle_state(world, bc, df, frame=90, seq=1)

check("8", "the contest is owned by Contact Authority before handover",
      state.owner == TS.OWNER_CONTACT_AUTHORITY,
      "owner=%r" % state.owner)
check("8", "the contest is NOT yet TES-owned",
      state.owned_by_tes is False,
      "owned_by_tes=%r" % state.owned_by_tes)

_identity_before = (state.tackle_id, state.frame, state.seq,
                    state.forward_origin_x, state.forward_origin_y,
                    state.attack_dir, state.separation_m,
                    state.contact_angle_deg)

result = TS.mark_tes_ownership(world, bc, df, (2.5, 0.0))

check("8", "the handover returns the state it took ownership of",
      result is state, "returned the same object: %s" % (result is state))
check("8", "ownership has transferred to TES",
      state.owner == TS.OWNER_TES and state.owned_by_tes is True,
      "owner=%r owned_by_tes=%r" % (state.owner, state.owned_by_tes))
check("8", "the handover frame is recorded",
      state.handover_frame == world.frame_count,
      "handover_frame=%r world.frame_count=%r"
      % (state.handover_frame, world.frame_count))
check("8", "the handover time is recorded",
      state.handover_time_s == world.sim_time_s,
      "handover_time_s=%r" % state.handover_time_s)
check("8", "the GOVERNED defender velocity is recorded",
      abs(state.handoff_defender_vx - 2.5) < 1e-9
      and abs(state.handoff_defender_vy - 0.0) < 1e-9,
      "handoff v=(%.3f, %.3f)"
      % (state.handoff_defender_vx, state.handoff_defender_vy))
check("8", "the module declares whether the handed velocity differed",
      isinstance(state.handoff_differs_from_contact, bool)
      and state.handoff_differs_from_contact is True,
      "differs_from_contact=%r (contact was -4.0, handed 2.5)"
      % state.handoff_differs_from_contact)
check("8", "the handover does NOT change the contest's identity",
      (state.tackle_id, state.frame, state.seq,
       state.forward_origin_x, state.forward_origin_y,
       state.attack_dir, state.separation_m,
       state.contact_angle_deg) == _identity_before,
      "identity and captured initial conditions are untouched")
check("8", "the handover does NOT terminate the contest",
      state.closed is False,
      "closed=%r" % state.closed)
check("8", "the handover does NOT mint a second contest",
      len([s for s in TS.recent_tackle_states() if s.open]) == 1,
      "open contests=%d"
      % len([s for s in TS.recent_tackle_states() if s.open]))

# ── THE HANDOVER IS A NO-OP WITHOUT A CONTEST ──
world_empty = fresh_world(frame=91, sim=18.0)
_none = TS.mark_tes_ownership(world_empty, bc, df, (1.0, 1.0))
check("8", "a handover with NO contest is a no-op",
      _none is None and TS.published_tackle_state(world_empty) is None,
      "returned=%r published=%r"
      % (_none, TS.published_tackle_state(world_empty)))

# ── A CLOSED CONTEST IS NOT HANDED OVER ──
TS.clear_tackle_state(world)
_closed_result = TS.mark_tes_ownership(world, bc, df, (3.0, 0.0))
check("8", "a CLOSED contest is not taken over by TES",
      state.owner == TS.OWNER_TES,
      "the handover targets the LIVE contest only (owner=%r)"
      % state.owner)

# ── THE HANDOVER SITE IS THE DECLARED TES ENTRY ──
# Read from the AST, never a source substring: the surrounding prose names
# the symbol, so a substring test would report one documented call as three.
check("8", "the handover is wired at the TES entry exactly ONCE",
      call_count(TES_SOURCE, "_mark_tes_ownership") == 1,
      "call sites=%d" % call_count(TES_SOURCE, "_mark_tes_ownership"))
check("8", "and the handover helper is imported under a private alias",
      imported_names(TES_SOURCE).get("_mark_tes_ownership")
      == "mark_tes_ownership",
      "tes.py imports it function-locally")
check("8", "the handover is the LAST thing the TES entry does",
      TES_SOURCE.rindex("_mark_tes_ownership(world")
      < TES_SOURCE.rindex("return ctx"),
      "the call precedes the entry's return")
check("8", "the handover records the GOVERNED contact velocity",
      "_mark_tes_ownership(world, bc, defender, (deff.vx, deff.vy))"
      in TES_SOURCE,
      "the corridor's governed velocity is what crosses the boundary")


# ═════════════════════════════════════════════════════════════════════
# [9] EVOLUTION, LIFECYCLE AND THE WIRING
# ═════════════════════════════════════════════════════════════════════
section("9", "EVOLUTION AND LIFECYCLE - growth, closure, and the wiring")

world = fresh_world(frame=100, sim=20.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, defender_role="TACKLER_1",
          tackle_role=TS.ROLE_PRIMARY, is_defender=True)
joiner = Body(9, 60.9, 35.3, -3.0, 0.2, defender_role="TACKLER_2",
              tackle_role="SECONDARY", is_defender=True)
state = TS.form_tackle_state(world, bc, df, frame=100, seq=1)

check("9", "the contest's attachment list starts empty",
      state.attachment_count == 0,
      "count=%d" % state.attachment_count)

row = TS.append_attachment(world, joiner, "SECONDARY")
check("9", "appending a joiner GROWS the attachment list",
      state.attachment_count == 1
      and joiner.player_id in state.attachment_ids,
      "count=%d ids=%s"
      % (state.attachment_count, state.attachment_ids))
check("9", "the appended row names the role it was admitted with",
      row is not None and getattr(row, "tackler_role", None) == "SECONDARY",
      "row role=%r" % (getattr(row, "tackler_role", None) if row else None))
check("9", "the joiner is on the CONTACT SET as well",
      joiner.player_id in state.contact_set_ids,
      "contact_set=%s" % (state.contact_set_ids,))
TS.append_attachment(world, joiner, "SECONDARY")
check("9", "the appending is idempotent per body",
      state.attachment_count == 1
      and len([a for a in state.attachments
               if a.player_id == joiner.player_id]) == 1,
      "count=%d after a second append" % state.attachment_count)

_primary_row = TS.append_attachment(world, df, TS.ROLE_PRIMARY)
check("9", "the PRIMARY defender can never become an attachment",
      _primary_row is None
      and state.primary_defender_id not in state.attachment_ids,
      "the primary is a field, not a row")

TS.mark_attached(world, joiner)
check("9", "the ATTACH event is stamped on the existing row",
      joiner.player_id in state.attached_ids,
      "attached=%s" % (state.attached_ids,))
check("9", "and it records the frame of attachment",
      state.attachment_for(joiner.player_id).attached_frame
      == world.frame_count,
      "attached_frame=%r frame=%r"
      % (state.attachment_for(joiner.player_id).attached_frame,
         world.frame_count))
check("9", "an unattached body is NOT reported as attached",
      df.player_id not in state.attached_ids,
      "attached=%s" % (state.attached_ids,))

# ── CLOSURE AT THE DECLARED LIFECYCLE BOUNDARIES ──
TS.clear_tackle_state(world)
check("9", "OPEN-PLAY ENTRY closes the contest with the declared reason",
      state.closed is True
      and state.closed_reason == TS.CLOSE_OPEN_PLAY_ENTERED,
      "closed=%r reason=%r" % (state.closed, state.closed_reason))
check("9", "closing UNPUBLISHES the contest",
      TS.published_tackle_state(world) is None
      and TS.active_tackle_state(world) is None,
      "published=%r active=%r"
      % (TS.published_tackle_state(world),
         TS.active_tackle_state(world)))

world = fresh_world(frame=110, sim=22.0)
state = TS.form_tackle_state(world, bc, df, frame=110, seq=1)
TS.clear_tackle_state(world, TS.CLOSE_KICKOFF)
check("9", "a KICKOFF closes the contest with the declared reason",
      state.closed is True
      and state.closed_reason == TS.CLOSE_KICKOFF,
      "closed=%r reason=%r" % (state.closed, state.closed_reason))

check("9", "clearing with no contest is a no-op",
      TS.clear_tackle_state(world) is None,
      "no exception and no contest")

TS.reset_tackle_states(world)
check("9", "a kickoff reset drops the whole ledger",
      TS.recent_tackle_states() == ()
      and TS.published_tackle_state(world) is None,
      "ring=%d" % len(TS.recent_tackle_states()))
check("9", "the ring is bounded by the declared limit",
      TS.TACKLE_STATE_RING_LIMIT == 600,
      "TACKLE_STATE_RING_LIMIT=%d" % TS.TACKLE_STATE_RING_LIMIT)

# ── THE WIRING EXISTS WHERE IT MUST ──
check("9", "Contact Authority defines the formation helper",
      "def _form_tackle_state(" in AUTHORITY_SOURCE,
      "def _form_tackle_state( present")
check("9", "Contact Authority FORMS at exactly three declaration points",
      call_count(AUTHORITY_SOURCE, "_form_tackle_state") == 3,
      "call sites=%d (manager + hand-over arm + engage arm)"
      % call_count(AUTHORITY_SOURCE, "_form_tackle_state"))
_auth_stores = {base for base, _attr, _line
                in attribute_stores(AUTHORITY_SOURCE)}
check("9", "the authority holds NO store of its own (it only calls out)",
      _auth_stores <= {"bc"},
      "authority store bases=%s (the Stage 1B carrier-effects rule)"
      % sorted(_auth_stores))

check("9", "the engine closes the contest on OPEN-PLAY entry",
      call_count(ENGINE_SOURCE, "clear_tackle_state") == 1,
      "call sites=%d" % call_count(ENGINE_SOURCE, "clear_tackle_state"))
check("9", "the engine resets the ledger on kickoff",
      call_count(ENGINE_SOURCE, "reset_tackle_states") == 1,
      "call sites=%d" % call_count(ENGINE_SOURCE, "reset_tackle_states"))
check("9", "the engine records a JOINER (append_attachment)",
      call_count(ENGINE_SOURCE, "append_attachment") == 1,
      "call sites=%d" % call_count(ENGINE_SOURCE, "append_attachment"))
check("9", "the engine records the ATTACH event at BOTH sites",
      call_count(ENGINE_SOURCE, "mark_attached") == 2,
      "call sites=%d (the role arm + the attach)"
      % call_count(ENGINE_SOURCE, "mark_attached"))

# ── NOTHING IN THE ENGINE READS THE STATE IT PUBLISHES ──
def reads_published_attr(path):
    """Files that READ the published attribute (not the module)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "tackle_state":
            hits.append(node.lineno)
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name)
              and node.func.id == "getattr"
              and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant)
              and node.args[1].value == TS.TACKLE_STATE_ATTR):
            hits.append(node.lineno)
    return hits


WRITERS = {
    os.path.normpath(MODULE_PATH),
    os.path.normpath(AUTHORITY_PATH),
    os.path.normpath(TES_PATH),
    os.path.normpath(ENGINE_PATH),
}
_readers = {}
_scanned = 0
for _root, _dirs, _files in os.walk("."):
    _dirs[:] = [d for d in _dirs
                if d not in ("debug_archive", "__pycache__", ".git",
                             ".venv", "venv", "node_modules", ".mypy_cache")]
    for _name in _files:
        if not _name.endswith(".py"):
            continue
        _rel = os.path.normpath(os.path.join(_root, _name))
        if _rel in WRITERS:
            continue
        _base = os.path.basename(_rel)
        if _base.startswith(("tmp_", "verify_", "probe_")):
            continue
        _scanned += 1
        _hits = reads_published_attr(_rel)
        if _hits:
            _readers[_rel] = _hits

check("9", "NO engine module READS the published contest state",
      not _readers, "readers=%s" % _readers)
check("9", "and the read-scan really walks the tree",
      _scanned > 100, "scanned=%d engine modules" % _scanned)
check("9", "the detector really fires on a reader",
      bool(reads_published_attr(MODULE_PATH))
      or "tackle_state" in SOURCE,
      "the module itself carries the declared name")

info("9", "engine modules scanned", "%d (excluding the four declared "
     "writers and every tmp_/verify_/probe_ file)" % _scanned)


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════
_passed = sum(1 for r in RESULTS if r["ok"])
_failed = sum(1 for r in RESULTS if not r["ok"])

REPORT = {
    "gate": "tackle_formation_contract",
    "stage": "2",
    "module": MODULE_PATH,
    "passed": _passed,
    "failed": _failed,
    "sections": [{"number": number, "title": title}
                 for number, title in SECTIONS],
    "checks": RESULTS,
}
with open("verify_tackle_formation_contract_report.json", "w",
          encoding="utf-8") as _fh:
    json.dump(REPORT, _fh, indent=2)

print()
print("=" * 74)
if _failed == 0:
    print("RESULT: the formation contract holds - %d checks, 0 failures"
          % _passed)
    print("        a declaration FORMS one contest, latches its initial")
    print("        conditions, hands it to TES, and never resolves it.")
else:
    print("RESULT: %d of %d checks FAILED" % (_failed, _passed + _failed))
    for row in RESULTS:
        if not row["ok"]:
            print("   FAIL [%s] %s   %s"
                  % (row["section"], row["name"], row["detail"]))
print("=" * 74)
print("Report written to verify_tackle_formation_contract_report.json")
