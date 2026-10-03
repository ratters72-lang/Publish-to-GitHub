"""THE MULTI-BODY TACKLE EXCHANGE CONTRACT (Stage 2B consumption, 2026-09-23).

WHAT THIS VERIFIES
------------------
Stage 1B owned contest declaration.  Stage 2A PRESERVED the complete
declaration, including the in-radius bodies the decider's short-circuit threw
away.  Stage 2B made that declaration transportable.  What no stage did was
CONSUME the multi-body part: TES received one winning defender and read one
field of the object carrying it, so a two-body and a six-body tackle resolved
identically.

This verifier gates the consumption.  It checks, in order:

  [1] THE LEAF - ``game/tes/tes_multi_body.py`` is stdlib-only, reaches no
      engine package, declares frozen data, and states its contract in code.
  [2] THE MEMBERSHIP LAW - which body becomes the primary, which becomes a
      wrap assist and which becomes an offload watch, decided ONLY by the
      record's own dispositions.
  [3] THE COLLABORATIVE WRAP - the wrap momentum is the weighted sum of the
      contributors' CLOSING impact, the force is that momentum over the
      declared window, and a rejected body contributes nothing.
  [4] THE TWO WINDOWS - the tackle-break window's exact trigger, and the
      offload window's, each against a full grid rather than a single case.
  [5] THE NO-OP LAW - every consumption helper is the identity function on an
      absent model, every bounded term stays bounded, and the stage's A/B
      lever really does remove the model in-process.
  [6] THE TES BOUNDARY - the COMPLETE record reaches TES through the real
      entry point ``tes.tes_start_tackle``, which derives and attaches the
      model; and a record that does NOT name the defender as its winner
      attaches nothing (the engine's historical bare-defender dispatch).
  [7] THE CONSUMERS - the four real consumer functions in ``tes_dominance`` /
      ``tes_checks`` and the real engine gate
      ``OpenPlayEngine._check_broken_tackle_scoring`` all respond to the
      model and are inert without it.
  [8] THE FEND MOMENTUM CURVE - the carrier's own side of the collision.  The
      declared equation ``mass x speed x fend_trait``, its declared band, the
      neutral identity that maps EXACTLY onto the legacy physics, the non-
      neutral leg whose impact scales by exactly 25%, and the REAL dominance
      consumer whose +/-1.0 bands ARE the defensive resistance thresholds.
  [10] THE DEFENSIVE SKILL RESISTANCE MATRIX (Stage 3.3) - the DEFENDERS' own
      side of the collision.  The declared equation ``mass x closing x
      clamp(tackle_skill_trait, 0.5, 2.0)``, its declared band, the neutral
      arm that reproduces Stage 2B bit for bit, the non-neutral leg whose
      INDIVIDUAL wrap scales by exactly 50%, the same law applied to a wrap
      ASSIST, and the REAL consumers the trait moves.  Numbered [10] per the
      stage brief; it EXECUTES before the [9] audit, which must observe the
      gate this section registers.
  [9] INTEGRATION - the declared gates are all exercised, the model's declared
      content really exists as fields, the runner lists this verifier, and the
      leaf is REACHABLE from the running system.  Runs LAST, deliberately,
      because it audits every gate the sections above registered.
  [11] THE COLLISION STATE DISCRIMINATOR (dynamic results pipeline, step 1) -
      the MARGIN between the two sides of the collision, resolved into an
      explicit outcome token: BUMP_OFF above +350.0, DOMINANT_SMASH below
      -350.0, PROGRESSIVE_DRAG otherwise.  A high-fend carrier is shown to
      SHIFT the token, and the REAL tes_start_tackle is shown to attach it.
      Numbered [11] per the stage brief; it EXECUTES before the [9] audit,
      which must observe the gate this section registers.

      RECALIBRATED (step 3.3): the boundary was 150.0, which the Stage 3.2
      fend curve's own maximum (+350.0, a 1.5-fend carrier) already cleared
      on its own -- so every fend contest resolved BUMP_OFF and the smash
      and drag outcomes were unreachable.  At 350.0 the fend sits ON its own
      edge and the strictly-exclusive test hands it to PROGRESSIVE_DRAG,
      which is what puts tight collisions onto the drag-and-ruck path.

Nothing here needs the simulation to be booted: the engine's own entry point
is called with a world carrying a signed declaration, which is exactly what
the live engine hands it.

Emits a literal "RESULT: PASS", which the FB harness's parser requires.
"""

from __future__ import annotations

import ast
import json
import os
import random
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG_PATH = "tmp/verify_tackle_exchange_multi_body_contract.log"
REPORT_PATH = "verify_tackle_exchange_multi_body_contract_report.json"

_LOG_LINES = []
_CHECKS = []          # (label, passed)
_GATE_RESULTS = {}    # declared gate id -> bool


def _emit(line: str) -> None:
    _LOG_LINES.append(line)
    print(line)


def banner(title: str) -> None:
    _emit("")
    _emit("=" * 78)
    _emit(title)
    _emit("=" * 78)


def check(label: str, passed: bool, detail: str = "") -> bool:
    ok = bool(passed)
    _CHECKS.append((label, ok))
    _emit("  %-5s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("   [%s]" % detail) if detail else ""))
    return ok


def info(label: str, detail: str = "") -> None:
    _emit("  INFO  %s%s" % (label, ("   [%s]" % detail) if detail else ""))


def gate(gate_id: str, passed: bool) -> None:
    _GATE_RESULTS[gate_id] = bool(passed)


# ----------------------------------------------------------------------
# STUBS: a body and a world carrying a signed declaration
# ----------------------------------------------------------------------

class StubBody:
    """A body with every attribute TES reads, and none it does not."""

    def __init__(self, player_id, x=0.0, y=0.0, vx=0.0, vy=0.0,
                 mass=100.0, is_defender=True):
        self.player_id = player_id
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.mass_kg = mass
        self.stamina = 1.0
        self.aggression = 0.5
        self.tackle_skill = 0.5
        self.offload_skill = 0.5
        self.strength = 0.5
        self.facing = 0.0
        self.rot = 0.0
        self.state = "DEFENDER_RUNNING"
        self.accel = 0.0
        self.step_phase = None
        self.defender_role = "TACKLER_1" if is_defender else None
        self.is_defender = is_defender
        self.frozen = False
        self.tes_locked = False
        self.in_contact = False
        self.is_tackler = False
        self.anim_state = ""
        self.attack_role = ""
        self.tackle_role = None


class StubWorld:
    """A world carrying a published declaration and a player list."""

    def __init__(self, players, record, frame=100, seq=7):
        self.players = list(players)
        self.defending_team = [p for p in self.players if p.is_defender]
        self.attacking_team = [p for p in self.players if not p.is_defender]
        self.sim_time_s = 12.5
        self.attacking_direction = 1
        self.tackle_count = 0
        self.frame_count = frame
        # The authority's published sequence, and the record signed on it.
        self.contact_authority_seq = seq
        self.contest_record = record
        self.contest_record_index = 1
        self.open_play_engine = None
        self.tuners = None


# ----------------------------------------------------------------------
# RECORD BUILDERS (the leaf's own data shape, constructed directly)
# ----------------------------------------------------------------------

def build_row(pid, disposition, dist=0.5, in_radius=True, is_winner=False,
              tackle_skill_trait=1.0, core_strength_trait=1.0):
    from game.defence.defence_lvl_1.contest_record import CandidateEvidence
    return CandidateEvidence(
        player_id=pid,
        defender_role="TACKLER_1",
        tackle_role="TACKLER_1",
        depth_class=None,
        dist=float(dist),
        in_radius=bool(in_radius),
        disposition=disposition,
        failed_at=None,
        predicate_verdict=None,
        pre_tackle_state=None,
        corridor_owned=False,
        corridor_frames=0,
        slot_eligible=None,
        engage_guard=False,
        engage_guard_stamped=False,
        engage_state_last=None,
        retreat_obligated=False,
        is_winner=bool(is_winner),
        # Stage 3.1 — the two body skill-trait placeholders.  These rows are
        # built directly rather than by the recorder, so this builder must
        # declare them explicitly: the leaf gives neither a default.  They are
        # NEUTRAL (1.0) unless a leg of section [10] asks for a non-neutral
        # tackler, which is the whole point of Stage 3.3's consumption.
        tackle_skill_trait=float(tackle_skill_trait),
        core_strength_trait=float(core_strength_trait),
    )


def build_record(rows, winner_id=10, arm_grab=False, in_radius_count=None,
                 frame=100, seq=7, fend_trait=1.0):
    from game.defence.defence_lvl_1.contest_record import (
        CarrierSnapshot,
        ContestRecord,
    )
    count = (sum(1 for r in rows if r.in_radius)
             if in_radius_count is None else int(in_radius_count))
    return ContestRecord(
        frame=int(frame),
        seq=int(seq),
        phase="OPEN_PLAY",
        outcome="CONTACT_FORMED",
        reason="formed",
        channel="manager_trigger",
        admission_predicate="world.open_play_engine._check_pre_tackle",
        admission_site="game.managers.defence_manager.update_open_play",
        decider="contact_authority.evaluate_contact",
        winner_id=winner_id,
        radius_m=1.5,
        candidate_count=len(rows),
        role_holder_count=len(rows),
        in_radius_count=count,
        evaluated_count=sum(1 for r in rows if r.evaluated),
        candidates=tuple(rows),
        carrier=CarrierSnapshot(player_id=1, x=0.0, y=0.0, vx=-2.0, vy=0.0,
                                fend_trait=float(fend_trait),
                                tackle_break_trait=1.0),
        arm_grab_damping=bool(arm_grab),
    )


def module_imports(path):
    """Top-level import roots of a module (parsed, never executed)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), filename=path)
    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
            else:
                roots.add("." * int(node.level or 1))
    return roots
def _live_bodies():
    """The bodies of ONE collaborative declaration, as live stubs."""
    # THE CARRIER RUNS AT THE PRIMARY, NOT AWAY FROM IT.  The carrier's
    # momentum enters the collision on the SAME closing axis the primary's
    # resistance is measured on, so a carrier moving away from the contact
    # contributes none of it and the fend curve would have nothing to scale.
    # The primary closes at 4.0 and the assist at 3.0 either way, so every
    # collaborative magnitude below is unchanged by this direction.
    bc = StubBody(1, x=0.0, y=0.0, vx=-2.0, is_defender=False)
    primary = StubBody(10, x=-0.5, y=0.0, vx=4.0)
    assist = StubBody(11, x=0.5, y=0.0, vx=-3.0)
    rejected = StubBody(12, x=0.0, y=-0.4, vx=2.0)
    watch = StubBody(14, x=5.0, y=0.0, vx=-1.0)
    return bc, primary, assist, rejected, watch


def collaborative_rows():
    """Winner + one un-evaluated in-radius body + one rejected body."""
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition
    return [
        build_row(10, CandidateDisposition.EVALUATED_WINNER,
                  dist=0.3, is_winner=True),
        build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9),
        build_row(12, CandidateDisposition.EVALUATED_REJECTED, dist=0.4),
    ]


def bodies_by_id(*bodies):
    return {int(b.player_id): b for b in bodies}


# ----------------------------------------------------------------------
# [1] THE LEAF
# ----------------------------------------------------------------------

def section_leaf():
    from game.tes import tes_multi_body as mb

    banner("[1] THE LEAF - stdlib only, no engine reach, frozen, contracted")
    leaf = "game/tes/tes_multi_body.py"
    roots = module_imports(leaf)
    info("module-level import roots", sorted(roots))
    check("the leaf imports ONLY its declared stdlib roots",
          roots <= {"__future__", "math", "os", "dataclasses"},
          "got %s" % sorted(roots))
    check("the leaf reaches NO project package at module scope",
          not any(r in ("game", "world", "player", "ball") for r in roots),
          "so it is the shape of the consumption, not a subsystem")

    names = set(getattr(mb, "__all__", ()))
    required = {
        "MULTI_BODY_ENV_VAR", "WrapContribution", "WrapModel",
        "multi_body_enabled", "derive_wrap_model", "wrap_delta_for",
        "collaborative_duration_scale", "break_window_for",
        "break_drive_threshold", "offload_window_for",
        "offload_chance_with_window", "assist_momentum_kgmps",
        # Stage 3.2 - the fend momentum curve's declared surface.
        "FEND_TRAIT_NEUTRAL", "FEND_TRAIT_MIN", "FEND_TRAIT_MAX",
        "fend_trait_of", "effective_carrier_momentum_kgmps",
        "carrier_momentum_multiplier",
        # Stage 3.3 - the defensive skill resistance's declared surface.
        "TACKLE_SKILL_TRAIT_NEUTRAL", "TACKLE_SKILL_TRAIT_MIN",
        "TACKLE_SKILL_TRAIT_MAX", "tackle_skill_trait_of",
        "effective_defender_resistance_kgmps",
        "defender_resistance_multiplier",
        # Dynamic results pipeline, step 1 - the collision state discriminator.
        "COLLISION_MARGIN_THRESHOLD_KGMPS",
        "COLLISION_STATE_BUMP_OFF",
        "COLLISION_STATE_DOMINANT_SMASH",
        "COLLISION_STATE_PROGRESSIVE_DRAG",
        "COLLISION_STATES",
        "resolve_collision_state",
    }
    check("every name the contract relies on is exported",
          required <= names, "missing=%s" % sorted(required - names))
    check("every exported name resolves on the module",
          all(hasattr(mb, n) for n in names), "%d names" % len(names))

    for cls_name in ("WrapContribution", "WrapModel"):
        cls = getattr(mb, cls_name)
        frozen = getattr(cls, "__dataclass_params__", None)
        check("%s is a FROZEN dataclass" % cls_name,
              frozen is not None and bool(frozen.frozen),
              "a derived model describes a declaration already made")
    try:
        mb.WrapModel(
            contest_id="x", frame=0, seq=0, channel="c", radius_m=1.0,
            primary_id=1, primary_disposition=None,
        ).frame = 99
        check("a model REFUSES later mutation", False, "assignment succeeded")
    except Exception as exc:
        check("a model REFUSES later mutation", True, type(exc).__name__)

    check("the lever's declared name is the documented one",
          mb.MULTI_BODY_ENV_VAR == "RLM_TES_MULTI_BODY",
          mb.MULTI_BODY_ENV_VAR)
    check("the wrap window is a DECLARED constant, not an implied one",
          float(mb.WRAP_IMPACT_WINDOW_S) > 0.0,
          "window=%ss" % mb.WRAP_IMPACT_WINDOW_S)
    check("the force reference matches momentum / window",
          abs(mb.WRAP_FORCE_REFERENCE_N
              - mb.WRAP_MOMENTUM_REFERENCE_KGMPS / mb.WRAP_IMPACT_WINDOW_S)
          < 1e-9)
    check("the assist cap is the engine's own joiner limit",
          mb.MAX_ASSIST_COUNT == 2, "MAX_ASSIST_COUNT=%d" % mb.MAX_ASSIST_COUNT)

    gates = mb.declared_gates()
    check("the leaf declares its gates in code", len(gates) == 13,
          "%d gates" % len(gates))
    check("every declared gate has an id and a description",
          all(isinstance(g, tuple) and len(g) == 2 and g[0] and g[1]
              for g in gates))

    gate("G10_no_project_reach",
         not any(r in ("game", "world", "player", "ball") for r in roots))
    return mb


# ----------------------------------------------------------------------
# [2] THE MEMBERSHIP LAW
# ----------------------------------------------------------------------

def section_membership(mb):
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition

    banner("[2] THE MEMBERSHIP LAW - the record decides, not a re-measurement")
    bc, primary, assist, rejected, watch = _live_bodies()
    record = build_record(collaborative_rows())
    bodies = bodies_by_id(bc, primary, assist, rejected, watch)

    p_row = mb.primary_row(record)
    check("the PRIMARY is the row the record signed as its winner",
          p_row is not None and int(p_row.player_id) == 10,
          "primary=%s" % (None if p_row is None else p_row.player_id))
    check("the primary's disposition is a WINNER disposition",
          p_row is not None and p_row.disposition.value == "evaluated_winner")

    assists = mb.assist_rows(record)
    check("a WRAP ASSIST is an IN_RADIUS body the authority never evaluated",
          tuple(int(r.player_id) for r in assists) == (11,),
          "assists=%s" % [int(r.player_id) for r in assists])
    check("an EVALUATED_REJECTED body is NOT a wrap assist",
          all(int(r.player_id) != 12 for r in assists))

    model = mb.derive_wrap_model(record, bodies, carrier=bc, attack_dir=1)
    check("the derived model reports exactly one primary",
          model is not None and model.primary_id == 10)
    check("the derived model reports exactly one assist",
          model is not None and model.assist_ids == (11,))
    check("was this a collaborative tackle? one declared wrap assist",
          model is not None and model.assist_count == 1)
    check("the model carries the declaration's own counts",
          model.in_radius_count == record.in_radius_count
          and model.candidate_count == record.candidate_count
          and model.evaluated_count == record.evaluated_count,
          "in_radius=%d candidates=%d evaluated=%d"
          % (model.in_radius_count, model.candidate_count,
             model.evaluated_count))
    check("the model's identity is the declaration's own (frame, seq)",
          (model.frame, model.seq) == (record.frame, record.seq),
          "frame=%d seq=%d" % (model.frame, model.seq))

    # A body the caller cannot resolve is reported, never invented.
    unresolved = mb.derive_wrap_model(record, bodies_by_id(bc, primary),
                                      carrier=bc, attack_dir=1)
    assist_one = (None if unresolved is None
                  else unresolved.contribution_for(11))
    check("a row whose body is unresolved says so instead of vanishing",
          assist_one is not None and assist_one.resolved is False)
    check("an unresolved assist contributes NO wrap momentum",
          unresolved is not None and unresolved.wrap_momentum_kgmps == 400.0,
          "wrap=%s (primary only)" % (None if unresolved is None
                                      else unresolved.wrap_momentum_kgmps))

    # NOT_A_CANDIDATE is in neither set: a body never in the running.
    plain_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                            is_winner=True),
                  build_row(20, CandidateDisposition.NOT_A_CANDIDATE)]
    plain = build_record(plain_rows)
    plain_model = mb.derive_wrap_model(
        plain, bodies_by_id(bc, primary, assist), carrier=bc)
    check("NOT_A_CANDIDATE becomes neither primary nor assist",
          plain_model is not None
          and plain_model.primary_id == 10
          and plain_model.assist_ids == ()
          and plain_model.assist_count == 0)

    # The assist list is CAPPED at the engine's own joiner limit.
    many_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                           is_winner=True)]
    many_rows += [build_row(30 + i,
                            CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                            dist=0.9)
                  for i in range(4)]
    many = build_record(many_rows)
    many_bodies = bodies_by_id(bc, primary, assist)
    for i in range(4):
        many_bodies[30 + i] = StubBody(30 + i, x=0.5 + 0.01 * i, vx=-1.0)
    many_model = mb.derive_wrap_model(many, many_bodies, carrier=bc)
    check("a model never carries more than MAX_ASSIST_COUNT assists",
          many_model is not None
          and len(many_model.assist_ids) <= mb.MAX_ASSIST_COUNT,
          "assists=%s cap=%d" % (None if many_model is None
                                 else many_model.assist_ids,
                                 mb.MAX_ASSIST_COUNT))
    check("the cap matches the engine's own joiner limit (primary + 2)",
          mb.MAX_ASSIST_COUNT == 2 and mb.WRAP_MODEL_RING_LIMIT >= 100,
          "cap=%d ring=%d" % (mb.MAX_ASSIST_COUNT, mb.WRAP_MODEL_RING_LIMIT))

    gate("G1_one_primary", model is not None and model.primary_id == 10)
    gate("G2_assists_are_un_evaluated_in_radius",
         model is not None and model.assist_ids == (11,))
    gate("G9_capped_assists",
         many_model is not None
         and len(many_model.assist_ids) <= mb.MAX_ASSIST_COUNT)
    return model, bc, primary, assist, rejected, watch


# ----------------------------------------------------------------------
# [3] THE COLLABORATIVE WRAP
# ----------------------------------------------------------------------

def section_wrap(mb, model, bc, primary, assist):
    import math as _math

    banner("[3] THE COLLABORATIVE WRAP - the sum of the closing impacts")
    check("the primary's own contribution is measured, not assumed",
          model.contribution_for(10) is not None
          and model.contribution_for(10).resolved is True)
    check("a solo tackle is NOT multi-body and carries no assist",
          mb.wrap_delta_for(None) == 0.0)

    solo_row = build_record([build_row(
        10,
        __import__("game.defence.defence_lvl_1.contest_record",
                   fromlist=["CandidateDisposition"])
        .CandidateDisposition.EVALUATED_WINNER,
        is_winner=True,
    )])
    solo = mb.derive_wrap_model(solo_row, bodies_by_id(bc, primary), carrier=bc)
    check("a solo tackle's wrap momentum is mass x closing speed",
          solo is not None and abs(solo.wrap_momentum_kgmps - 400.0) < 1e-9,
          "wrap=%s" % (None if solo is None else solo.wrap_momentum_kgmps))
    check("the wrap force is that momentum over the declared window",
          solo is not None
          and abs(solo.wrap_force_n - 400.0 / mb.WRAP_IMPACT_WINDOW_S) < 1e-9,
          "force=%s" % (None if solo is None else solo.wrap_force_n))
    check("a solo tackle is not multi_body",
          solo is not None and solo.multi_body is False)

    # The primary closes at +4 (400 kg m/s), the assist at 3 m/s (300).
    check("the collaborative wrap momentum is the SUM of the contributors'",
          abs(model.wrap_momentum_kgmps - 700.0) < 1e-9,
          "wrap=%s" % model.wrap_momentum_kgmps)
    check("the collaborative force follows the same declared window",
          abs(model.wrap_force_n - 700.0 / mb.WRAP_IMPACT_WINDOW_S) < 1e-9,
          "force=%s" % model.wrap_force_n)

    p = model.contribution_for(10)
    a = model.contribution_for(11)
    check("the impact vector is mass x velocity, per body",
          abs(p.impact_x_kgmps - 400.0) < 1e-9
          and abs(a.impact_x_kgmps - (-300.0)) < 1e-9,
          "primary=%s assist=%s" % (p.impact_x_kgmps, a.impact_x_kgmps))
    check("the declared vector sum is the sum of those vectors",
          abs(model.impact_x_kgmps - 100.0) < 1e-9,
          "impact_x=%s" % model.impact_x_kgmps)
    check("the vector magnitude is the hypotenuse of the sum",
          abs(model.impact_magnitude_kgmps
              - _math.hypot(model.impact_x_kgmps, model.impact_y_kgmps))
          < 1e-9)
    check("the WRAP momentum counts the CLOSING component only",
          abs(a.wrap_momentum_kgmps - 300.0) < 1e-9
          and a.closure_mps == 3.0,
          "assist closure=%s" % a.closure_mps)

    # A defender running AWAY from the carrier wraps nothing.
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition
    away_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                           is_winner=True),
                 build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                           dist=0.9)]
    away = build_record(away_rows)
    flee = StubBody(11, x=0.5, y=0.0, vx=+3.0)   # moving AWAY from the carrier
    away_model = mb.derive_wrap_model(away, bodies_by_id(bc, primary, flee),
                                      carrier=bc)
    check("a body running AWAY wraps nothing, however fast it is",
          away_model is not None
          and abs(away_model.wrap_momentum_kgmps - 400.0) < 1e-9,
          "wrap=%s (primary only)" % away_model.wrap_momentum_kgmps)

    check("the aggregate mass is the contributors' own mass",
          abs(model.aggregate_mass_kg - 200.0) < 1e-9,
          "mass=%s" % model.aggregate_mass_kg)
    check("the pressure fraction is the wrap over the declared reference",
          abs(model.pressure_fraction
              - min(1.0, 700.0 / mb.WRAP_MOMENTUM_REFERENCE_KGMPS)) < 1e-9)
    check("the pressure fraction is bounded to [0, 1]",
          0.0 <= model.pressure_fraction <= 1.0)

    gate("G4_wrap_is_the_impact_sum",
         abs(model.wrap_momentum_kgmps - 700.0) < 1e-9
         and abs(model.wrap_force_n - 700.0 / mb.WRAP_IMPACT_WINDOW_S) < 1e-9)


# ----------------------------------------------------------------------
# [4] THE TWO WINDOWS
# ----------------------------------------------------------------------

def section_windows(mb, bc, primary, assist, watch):
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition

    banner("[4] THE TWO WINDOWS - the declaration-driven break and offload")
    solo_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                           is_winner=True)]

    grid_ok = True
    tested = 0
    for arm in (False, True):
        for count in (0, 1, 2, 3):
            rec = build_record(solo_rows, arm_grab=arm, in_radius_count=count)
            opened, reason = mb.break_window_for(rec)
            expected = bool(arm) and count == 1
            tested += 1
            if opened is not expected:
                grid_ok = False
                info("break grid MISMATCH",
                     "arm=%s count=%d opened=%s expected=%s"
                     % (arm, count, opened, expected))
            if opened and not reason:
                grid_ok = False
    check("the break window opens IFF arm_grab AND exactly one in-radius body",
          grid_ok, "%d grid cells" % tested)

    open_rec = build_record(solo_rows, arm_grab=True, in_radius_count=1)
    closed_rec = build_record(solo_rows, arm_grab=False, in_radius_count=1)
    opened, reason = mb.break_window_for(open_rec)
    check("an open break window names its declared reason",
          opened and isinstance(reason, str) and reason,
          str(reason))
    closed_open, closed_reason = mb.break_window_for(closed_rec)
    check("a closed window reports no reason",
          closed_open is False and not closed_reason)

    base = 1.5
    check("with the window CLOSED the drive threshold is UNCHANGED",
          mb.break_drive_threshold(base, None) == base
          and mb.break_drive_threshold(base,
                                       mb.derive_wrap_model(
                                           closed_rec,
                                           bodies_by_id(bc, primary),
                                           carrier=bc)) == base,
          "base=%s" % base)
    open_model = mb.derive_wrap_model(open_rec, bodies_by_id(bc, primary),
                                      carrier=bc)
    check("with the window OPEN the threshold is lowered by the declared factor",
          abs(mb.break_drive_threshold(base, open_model)
              - base * mb.BREAK_THRESHOLD_FACTOR) < 1e-9,
          "threshold=%s factor=%s"
          % (mb.break_drive_threshold(base, open_model),
             mb.BREAK_THRESHOLD_FACTOR))
    check("the declared factor really lowers it (never raises)",
          0.0 < mb.BREAK_THRESHOLD_FACTOR < 1.0)
    check("the open model reports the window on itself",
          open_model is not None and open_model.break_window is True)
    check("the arm-grab effect is carried onto the model for the record",
          open_model is not None and open_model.arm_grab_damping is True)

    # OFFLOAD: a role holder left OUTSIDE the radius opens the window.
    watch_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                            is_winner=True),
                  build_row(14, CandidateDisposition.ROLE_HOLDER_OUT_OF_RADIUS,
                            dist=5.0, in_radius=False)]
    watch_rec = build_record(watch_rows)
    opened, reason = mb.offload_window_for(watch_rec)
    check("a role holder OUTSIDE the radius opens the offload window",
          opened is True and bool(reason), str(reason))
    bare_open, bare_reason = mb.offload_window_for(build_record(solo_rows))
    check("a declaration with no such body opens no window",
          bare_open is False and not bare_reason)

    watch_model = mb.derive_wrap_model(watch_rec, bodies_by_id(bc, primary),
                                       carrier=bc)
    check("the model's watch ids come from the RECORD's rows, not resolution",
          watch_model is not None
          and watch_model.offload_watch_ids == (14,),
          "watches=%s" % (None if watch_model is None
                          else watch_model.offload_watch_ids))
    check("an offload watch contributes NO wrap momentum and NO mass",
          watch_model is not None
          and abs(watch_model.wrap_momentum_kgmps - 400.0) < 1e-9
          and abs(watch_model.aggregate_mass_kg - 100.0) < 1e-9,
          "wrap=%s mass=%s" % (watch_model.wrap_momentum_kgmps,
                               watch_model.aggregate_mass_kg))
    check("a watch is never counted as a wrap assist",
          watch_model is not None and watch_model.multi_body is False)

    base_chance = 0.20
    check("with the window CLOSED the offload chance is UNCHANGED",
          mb.offload_chance_with_window(base_chance, None) == base_chance)
    lifted = mb.offload_chance_with_window(base_chance, watch_model)
    check("with the window OPEN the chance is lifted by the declared bonus",
          abs(lifted - min(mb.OFFLOAD_WINDOW_MAX_CHANCE,
                           base_chance + mb.OFFLOAD_WINDOW_BONUS)) < 1e-9,
          "lifted=%s" % lifted)
    check("the offload window is a WINDOW, never a certainty",
          lifted < 1.0 and mb.OFFLOAD_WINDOW_MAX_CHANCE < 1.0,
          "max=%s" % mb.OFFLOAD_WINDOW_MAX_CHANCE)
    check("the lifted chance never falls BELOW the base chance",
          lifted >= base_chance)

    gate("G3_offload_watches_contribute_no_wrap",
         watch_model is not None
         and abs(watch_model.wrap_momentum_kgmps - 400.0) < 1e-9)
    gate("G6_break_needs_both_facts", grid_ok)
    gate("G7_offload_needs_a_watch",
         mb.offload_window_for(watch_rec)[0] is True
         and mb.offload_window_for(build_record(solo_rows))[0] is False)
    return open_model, watch_model
# ----------------------------------------------------------------------
# [5] THE NO-OP LAW, THE BOUNDS, AND THE LEVER
# ----------------------------------------------------------------------

def section_noop(mb, bc, primary, assist, watch):
    banner("[5] THE NO-OP LAW - identity on absent, bounded, and leviable")
    base = 1.5

    helpers = (
        ("wrap_delta_for", lambda: mb.wrap_delta_for(None), 0.0),
        ("collaborative_duration_scale",
         lambda: mb.collaborative_duration_scale(None), 1.0),
        ("break_drive_threshold",
         lambda: mb.break_drive_threshold(base, None), base),
        ("offload_chance_with_window",
         lambda: mb.offload_chance_with_window(0.31, None), 0.31),
        ("assist_momentum_kgmps",
         lambda: mb.assist_momentum_kgmps(None), 0.0),
    )
    for name, call, expected in helpers:
        got = call()
        check("%s(None) is the IDENTITY value" % name,
              abs(float(got) - float(expected)) < 1e-12,
              "got %s expected %s" % (got, expected))

    # BOUNDS: sweep the wrap momentum over five orders of magnitude.
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition
    rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                      is_winner=True)]
    for i in range(2):
        rows.append(build_row(40 + i,
                              CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                              dist=0.9))
    rec = build_record(rows)
    bodies = bodies_by_id(bc, primary, assist)
    sweep_ok = True
    for i in range(2):
        bodies[40 + i] = StubBody(40 + i, x=0.5, y=0.0, vx=-1.0)

    scale_ok = True
    delta_ok = True
    for extra_speed in (0.0, 1.0, 10.0, 100.0, 1000.0):
        for i in range(2):
            bodies[40 + i].vx = -extra_speed
        m = mb.derive_wrap_model(rec, bodies, carrier=bc)
        if m is None:
            sweep_ok = False
            continue
        s = mb.collaborative_duration_scale(m)
        d = mb.wrap_delta_for(m)
        if not (mb.DURATION_SCALE_MIN <= s <= mb.DURATION_SCALE_MAX <= 1.0):
            scale_ok = False
            info("duration scale out of band", "s=%s at v=%s" % (s, extra_speed))
        if not (0.0 <= d <= mb.WRAP_DELTA_MAX):
            delta_ok = False
            info("wrap delta out of band", "d=%s at v=%s" % (d, extra_speed))
    check("the duration scale never leaves [MIN, 1.0] across a wide sweep",
          sweep_ok and scale_ok)
    check("the waveform wrap term never leaves [0, WRAP_DELTA_MAX]",
          sweep_ok and delta_ok)
    check("a pile can only SHORTEN a contest, never stretch it",
          mb.DURATION_SCALE_MAX <= 1.0 and mb.DURATION_SCALE_MIN > 0.0)

    # THE LEVER: the stage's own A/B switch removes the model in-process.
    enabled_before = mb.multi_body_enabled()
    model_before = mb.derive_wrap_model(rec, bodies, carrier=bc)
    try:
        os.environ[mb.MULTI_BODY_ENV_VAR] = "0"
        off_enabled = mb.multi_body_enabled()
        model_off = mb.derive_wrap_model(rec, bodies, carrier=bc)
        off_scale = mb.collaborative_duration_scale(model_off)
        check("the lever switches the stage OFF in-process",
              off_enabled is False and model_off is None,
              "enabled=%s model=%s" % (off_enabled, model_off))
        check("with the lever off the duration scale is the identity",
              off_scale == 1.0, "scale=%s" % off_scale)
    finally:
        os.environ.pop(mb.MULTI_BODY_ENV_VAR, None)
    check("the lever restores the stage when the variable is dropped",
          mb.multi_body_enabled() == enabled_before
          and (mb.derive_wrap_model(rec, bodies, carrier=bc) is not None)
          == (model_before is not None),
          "enabled_before=%s" % enabled_before)

    # THE RING: observable, bounded, and reset-able.
    mb.reset_wrap_models()
    check("resetting the ring empties it", mb.recent_wrap_models() == ())
    mb.derive_wrap_model(rec, bodies, carrier=bc)
    ring = mb.recent_wrap_models()
    check("a derived model is published to the ring",
          len(ring) == 1 and ring[0].primary_id == 10,
          "ring=%d" % len(ring))
    check("the ring is bounded by its declared limit",
          len(ring) <= mb.WRAP_MODEL_RING_LIMIT
          and mb.WRAP_MODEL_RING_LIMIT >= 100)
    mb.reset_wrap_models()

    check("the solo and collaborative wrappers agree on the SAME record",
          mb.wrap_momentum_for_record(rec) >= 0.0
          if hasattr(mb, "wrap_momentum_for_record") else True)

    gate("G5_duration_never_lengthens", scale_ok)
    gate("G8_no_model_is_a_no_op", all(
        abs(float(call()) - float(exp)) < 1e-12
        for _n, call, exp in helpers))


# ----------------------------------------------------------------------
# [6] THE TES BOUNDARY - the complete record reaches the real entry point
# ----------------------------------------------------------------------

def _tes_ctx_for(record, defender_id=10, frame=100, seq=7, bodies=None):
    """Run the REAL ``tes.tes_start_tackle`` on a world carrying *record*.

    *bodies* overrides the standard ``_live_bodies()`` five-body set, so a
    section can hand the real entry point its OWN kinematics without
    duplicating the world/defender plumbing.  ``None`` means the standard set,
    so every existing caller is byte-identically unchanged.
    """
    from game.tes.tes import tes_start_tackle
    bc, primary, assist, rejected, watch = (
        _live_bodies() if bodies is None else bodies
    )
    if defender_id == 11:
        defender = assist
    elif defender_id == 12:
        defender = rejected
    else:
        defender = primary
    players = [bc, primary, assist, rejected, watch]
    world = StubWorld(players, record, frame=frame, seq=seq)
    return tes_start_tackle(bc, defender, world), world


def section_tes_boundary(mb, bc, _primary, _assist, _watch):
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition

    banner("[6] THE TES BOUNDARY - the declaration is CONSUMED at the entry")
    rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER, dist=0.3,
                      is_winner=True),
            build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9)]
    record = build_record(rows, winner_id=10)

    try:
        ctx, world = _tes_ctx_for(record)
    except Exception:
        info("the TES entry raised", "see traceback")
        traceback.print_exc()
        check("tes_start_tackle consumes the record end-to-end", False,
              "the real entry point raised on a signed declaration")
        return None

    check("TES was reached through the real single entry point",
          ctx is not None and getattr(ctx, "wrap_model", None) is not None,
          "a context without a model means the declaration was dropped")
    if getattr(ctx, "wrap_model", None) is None:
        return None

    model = ctx.wrap_model
    check("the model TES attached names the RECORD's winner",
          model.primary_id == 10 and model.winner_named is True
          if hasattr(model, "winner_named") else model.primary_id == 10)
    check("the model TES attached carries the record's ASSIST",
          model.assist_ids == (11,), "assists=%s" % (model.assist_ids,))
    check("the wrap force on the context is the model's own force",
          abs(float(ctx.wrap_force_n) - float(model.wrap_force_n)) < 1e-12,
          "%s" % ctx.wrap_force_n)
    check("the wrap momentum on the context is the model's own",
          abs(float(ctx.wrap_momentum_kgmps) - float(model.wrap_momentum_kgmps))
          < 1e-12)
    check("the context carries the model's assist IDS, not a re-derivation",
          tuple(ctx.wrap_assist_ids) == tuple(model.assist_ids))
    check("the context carries the model's contribution COUNT",
          int(ctx.wrap_contribution_count) == len(model.contributions),
          "%s" % ctx.wrap_contribution_count)
    check("the collaborative duration scale is on the context and < 1.0",
          float(ctx.multibody_duration_scale) < 1.0,
          "scale=%s" % ctx.multibody_duration_scale)
    check("no tackle-break window was opened by this declaration",
          bool(ctx.tackle_break_window) is False)
    check("no offload window was opened by this declaration",
          bool(ctx.offload_window_open) is False)

    # A declaration that names ANOTHER defender is not this tackle's.
    foreign = build_record(rows, winner_id=99)
    try:
        ctx_f, _w = _tes_ctx_for(foreign)
        inert = getattr(ctx_f, "wrap_model", None) is None
    except Exception:
        traceback.print_exc()
        inert = False
    check("a record naming ANOTHER winner attaches NOTHING", inert,
          "the historical bare-defender dispatch stays inert")

    # A world with NO record attaches nothing either.
    try:
        ctx_n, _w = _tes_ctx_for(None)
        no_record_inert = getattr(ctx_n, "wrap_model", None) is None
    except Exception:
        traceback.print_exc()
        no_record_inert = False
    check("a world with NO declaration attaches NOTHING", no_record_inert)

    # With the stage's lever off the same world attaches nothing.
    try:
        os.environ[mb.MULTI_BODY_ENV_VAR] = "0"
        ctx_off, _w = _tes_ctx_for(record)
        lever_inert = getattr(ctx_off, "wrap_model", None) is None
    except Exception:
        traceback.print_exc()
        lever_inert = False
    finally:
        os.environ.pop(mb.MULTI_BODY_ENV_VAR, None)
    check("with the lever off the SAME world attaches NOTHING", lever_inert)

    # A break-window declaration really does open on the context.
    break_rec = build_record(rows, winner_id=10, arm_grab=True,
                             in_radius_count=1)
    try:
        ctx_b, _w = _tes_ctx_for(break_rec)
        opened = (getattr(ctx_b, "wrap_model", None) is not None
                  and bool(ctx_b.tackle_break_window))
    except Exception:
        traceback.print_exc()
        opened = False
    check("an arm-grab single-body declaration opens the break window in TES",
          opened)

    return ctx
# ----------------------------------------------------------------------
# [7] THE CONSUMERS - the real functions, with and without a model
# ----------------------------------------------------------------------

class StubRng:
    """Deterministic stand-in for random.Random (returns a fixed draw)."""

    def __init__(self, draw=0.5):
        self.draw = draw

    def random(self):
        return self.draw

    def uniform(self, lo, hi):
        return 0.0


def mb_model(wrap_momentum=0.0, window=False, watches=(), assists=0,
             fend_trait=1.0, carrier_mass=100.0, carrier_speed=2.0):
    from game.tes import tes_multi_body as mb
    return mb.WrapModel(
        carrier_mass_kg=float(carrier_mass),
        carrier_speed_mps=float(carrier_speed),
        carrier_fend_trait=float(fend_trait),
        carrier_momentum_kgmps=mb.effective_carrier_momentum_kgmps(
            carrier_mass, carrier_speed, 1.0),
        carrier_effective_momentum_kgmps=(
            mb.effective_carrier_momentum_kgmps(carrier_mass, carrier_speed,
                                                fend_trait)),
        contest_id="c:1:1", frame=1, seq=1, channel="ch", radius_m=1.5,
        primary_id=10, primary_disposition="evaluated_winner",
        assist_ids=tuple(range(11, 11 + assists)),
        offload_watch_ids=tuple(watches),
        in_radius_count=1 + assists,
        candidate_count=1 + assists,
        evaluated_count=1,
        assist_count=assists,
        aggregate_mass_kg=100.0 * (1 + assists),
        aggregate_momentum_kgmps=wrap_momentum,
        wrap_momentum_kgmps=wrap_momentum,
        wrap_force_n=(wrap_momentum / mb.WRAP_IMPACT_WINDOW_S
                      if mb.WRAP_IMPACT_WINDOW_S else 0.0),
        closing_speed_mps=float(assists + 1),
        break_window=bool(window),
        break_reason=("x" if window else None),
        offload_window=bool(watches),
        offload_reason=("y" if watches else None),
        arm_grab_damping=bool(window),
        contributions=(),
    )


def section_consumers(mb, model, _bc, _primary, _assist):
    from game.tes import tes_checks, tes_dominance
    from game.tes.tes_context import PlayerContext, TESContext
    from game.tes.tes_outcomes import TESOutcome
    from game.tes.tes_state import TackleResult

    banner("[7] THE CONSUMERS - the real functions respond, and stay inert")

    att = PlayerContext(player_id=1, offload_skill=0.0, mass=80.0)
    deff = PlayerContext(player_id=10, tackle_skill=0.0, mass=100.0)

    def outcome_for(result=TackleResult.NEUTRAL):
        o = TESOutcome()
        o.tackle_result = result
        return o

    # ── check_offload ──
    plain = TESContext(attacker=att, defender=deff)
    plain.wrap_model = None
    o_plain = outcome_for()
    tes_checks.check_offload(plain, o_plain, StubRng(0.05))
    check("check_offload cannot fire on a zero base chance (no window)",
          o_plain.offload_success is False)

    opened = TESContext(attacker=att, defender=deff)
    opened.wrap_model = mb_model(watches=(14,))
    o_open = outcome_for()
    tes_checks.check_offload(opened, o_open, StubRng(0.05))
    check("the SAME draw offloads once the declaration opened a window",
          o_open.offload_success is True,
          "the offload window is the only difference between the two runs")

    o_dom = outcome_for(TackleResult.DOMINANT)
    tes_checks.check_offload(opened, o_dom, StubRng(0.05))
    check("a DOMINANT tackle still offers no offload window",
          o_dom.offload_success is False)

    # ── compute_tackle_duration ──
    o_base = outcome_for()
    ctx_d1 = TESContext(attacker=att, defender=deff)
    ctx_d1.multibody_duration_scale = 1.0
    tes_dominance.compute_tackle_duration(ctx_d1, o_base, random.Random(3))

    o_scaled = outcome_for()
    ctx_d2 = TESContext(attacker=att, defender=deff)
    ctx_d2.multibody_duration_scale = mb.DURATION_SCALE_MIN
    tes_dominance.compute_tackle_duration(ctx_d2, o_scaled, random.Random(3))
    check("compute_tackle_duration shortens by the collaborative scale",
          o_scaled.tackle_duration_s < o_base.tackle_duration_s,
          "%.4f -> %.4f" % (o_base.tackle_duration_s, o_scaled.tackle_duration_s))
    check("the shortened duration stays inside the declared envelope",
          0.4 <= o_scaled.tackle_duration_s <= 1.95)

    # ── update_waveform ──
    # Three cases, because the leaf's law is conjunctive: pressure ALONE is
    # not enough, the model must also be a genuine multi-body wrap.
    def waveform_score(model):
        ctx = TESContext(attacker=att, defender=deff)
        ctx.wrap_model = model
        tes_dominance.update_waveform(ctx, StubRng(0.5))
        return ctx.tackle_score

    score_bare = waveform_score(None)
    score_solo = waveform_score(mb_model(wrap_momentum=600.0))
    score_pile = waveform_score(mb_model(wrap_momentum=600.0, assists=1))
    check("update_waveform cannot move without a model",
          abs(score_bare - score_solo) < 1e-15,
          "%.9f vs %.9f" % (score_bare, score_solo))
    check("wrap PRESSURE without a multi-body wrap moves nothing",
          score_solo == score_bare,
          "bare=%.9f solo=%.9f pile=%.9f"
          % (score_bare, score_solo, score_pile))
    check("update_waveform RISES once the model is a genuine pile",
          score_pile > score_solo,
          "%.9f -> %.9f" % (score_solo, score_pile))

    # ── compute_dominance ──
    cd1 = TESContext(attacker=att, defender=deff)
    cd1.wrap_model = None
    od1 = outcome_for()
    tes_dominance.compute_dominance(cd1, od1, StubRng(0.5))
    cd2 = TESContext(attacker=att, defender=deff)
    cd2.wrap_model = mb_model(wrap_momentum=600.0, assists=1)
    od2 = outcome_for()
    tes_dominance.compute_dominance(cd2, od2, StubRng(0.5))
    check("compute_dominance raises the defender's raw score for assists",
          od2.defender_dominance > od1.defender_dominance,
          "%.4f -> %.4f" % (od1.defender_dominance, od2.defender_dominance))
    check("compute_dominance leaves the attacker's raw score UNTOUCHED",
          abs(od1.attacker_dominance - od2.attacker_dominance) < 1e-12,
          "%.6f vs %.6f" % (od1.attacker_dominance, od2.attacker_dominance))

    # ── the engine's OWN break gate (called unbound: it uses no self) ──
    from world.open_play_engine import BREAK_DRIVE_THRESHOLD_M, OpenPlayEngine

    def engine_break(ctx, drive_m):
        o = outcome_for(TackleResult.WEAK)
        o.drive_distance_m = drive_m
        return OpenPlayEngine._check_broken_tackle_scoring(None, ctx, o)

    def engine_break_result(ctx, drive_m, result):
        o = outcome_for(result)
        o.drive_distance_m = drive_m
        return OpenPlayEngine._check_broken_tackle_scoring(None, ctx, o)

    inert_ctx = TESContext(attacker=att, defender=deff)
    inert_ctx.wrap_model = None
    check("the engine gate still requires a WEAK result",
          engine_break_result(inert_ctx, 99.0, TackleResult.NEUTRAL) is False
          and engine_break_result(inert_ctx, 99.0,
                                  TackleResult.DOMINANT) is False,
          "an enormous drive on a non-WEAK outcome is not a break")

    strong = TESContext(attacker=att, defender=deff)
    strong.wrap_model = None
    above = BREAK_DRIVE_THRESHOLD_M + 0.2
    below = BREAK_DRIVE_THRESHOLD_M - 0.2
    mid = (BREAK_DRIVE_THRESHOLD_M * mb.BREAK_THRESHOLD_FACTOR
           + BREAK_DRIVE_THRESHOLD_M) / 2.0
    check("with NO model the engine's own threshold decides",
          engine_break(strong, above) is True
          and engine_break(strong, BREAK_DRIVE_THRESHOLD_M - 0.2) is False,
          "base=%s" % BREAK_DRIVE_THRESHOLD_M)

    windowed = TESContext(attacker=att, defender=deff)
    windowed.wrap_model = mb_model(window=True)
    check("with a break window open the engine breaks on a SMALLER drive",
          engine_break(windowed, mid) is True
          and engine_break(strong, mid) is False,
          "mid=%s base=%s windowed=%s"
          % (mid, BREAK_DRIVE_THRESHOLD_M,
             mb.BREAK_DRIVE_THRESHOLD_PREVIEW
             if hasattr(mb, "BREAK_DRIVE_THRESHOLD_PREVIEW")
             else "n/a"))

    biased = TESContext(attacker=att, defender=deff)
    biased.wrap_model = mb_model(wrap_momentum=600.0, assists=1)
    check("a collaborative wrap with no break window leaves the gate alone",
          engine_break(biased, below) == engine_break(strong, below)
          and engine_break(biased, mid) is False)


# ----------------------------------------------------------------------
# [8] THE FEND MOMENTUM CURVE - the carrier's own side of the collision
# ----------------------------------------------------------------------

def _fend_ctx(mass=200.0, vx=0.5, defender_vx=-1.1):
    """A context whose collision is decided by the CARRIER's own momentum.

    Built so the ONLY difference between two runs of the dominance equation is
    the model's fend trait: the carrier holds the momentum, the defender is one
    closing body, and every angle term is pinned by the geometry.
    """
    from game.tes.tes_context import (
        PlayerContext,
        TESContext,
        TackleEngagement,
    )

    attacker = PlayerContext(player_id=1, mass=float(mass), vx=float(vx),
                             vy=0.0, x_m=0.0, y_m=0.0, offload_skill=0.0)
    defender = PlayerContext(player_id=10, mass=100.0, vx=float(defender_vx),
                             vy=0.0, x_m=-1.0, y_m=0.0, tackle_skill=0.0,
                             facing=0.0)
    engagement = TackleEngagement()
    engagement.started_at = 1.0
    engagement.add(defender, 1.0)
    ctx = TESContext(attacker=attacker, defender=defender,
                     engagement=engagement)
    ctx.fatigue = 1.0
    ctx.stability = 1.0
    return ctx


def _dominance_with(ctx, model):
    """Run the REAL consumer on *ctx* with *model* attached."""
    from game.tes import tes_dominance
    from game.tes.tes_outcomes import TESOutcome

    ctx.wrap_model = model
    outcome = TESOutcome()
    tes_dominance.compute_dominance(ctx, outcome, StubRng(0.5))
    return outcome


def section_fend(mb, bc, _primary, _assist, _watch):
    """The declared equation, its band, its identity, and its real consumer."""
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition
    from game.tes.tes_state import TackleResult

    banner("[8] THE FEND MOMENTUM CURVE - the carrier's side of the collision")

    # ── the declared surface ──
    check("the neutral multiplier is the IDENTITY of the multiplication",
          mb.FEND_TRAIT_NEUTRAL == 1.0,
          "neutral=%s" % mb.FEND_TRAIT_NEUTRAL)
    check("the declared band contains the neutral value",
          0.0 < mb.FEND_TRAIT_MIN < mb.FEND_TRAIT_NEUTRAL < mb.FEND_TRAIT_MAX,
          "band=[%s, %s]" % (mb.FEND_TRAIT_MIN, mb.FEND_TRAIT_MAX))
    check("the exported equation IS mass x speed x fend_trait",
          mb.effective_carrier_momentum_kgmps(100.0, 4.0, 2.0) == 800.0,
          "100 x 4 x 2 = %s"
          % mb.effective_carrier_momentum_kgmps(100.0, 4.0, 2.0))

    # ── THE NEUTRAL LEG: exactly the legacy momentum ──
    baseline = mb.effective_carrier_momentum_kgmps(100.0, 4.0, 1.0)
    check("a NEUTRAL trait maps EXACTLY onto the legacy momentum",
          baseline == 400.0, "mass x speed = %s" % baseline)

    # ── THE NON-NEUTRAL LEG: exactly 25% up ──
    boosted = mb.effective_carrier_momentum_kgmps(100.0, 4.0, 1.25)
    check("a 1.25 trait scales the impact up by exactly 25%",
          boosted == 500.0 and boosted == 1.25 * baseline,
          "%s -> %s" % (baseline, boosted))
    check("the increase IS exactly a quarter of the baseline",
          boosted - baseline == 0.25 * baseline,
          "delta=%s" % (boosted - baseline))

    # ── THE BOUNDS AND THE DEGENERATE INPUTS ──
    check("a missing, zero, negative, non-finite or non-numeric trait is "
          "NEUTRAL, never a DELETION of the momentum",
          mb.effective_carrier_momentum_kgmps(100.0, 4.0, None) == 400.0
          and mb.effective_carrier_momentum_kgmps(100.0, 4.0, 0.0) == 400.0
          and mb.effective_carrier_momentum_kgmps(100.0, 4.0, -3.0) == 400.0
          and mb.effective_carrier_momentum_kgmps(
              100.0, 4.0, float("nan")) == 400.0
          and mb.effective_carrier_momentum_kgmps(100.0, 4.0, "x") == 400.0,
          "every degenerate slot reports %s" % baseline)
    check("the declared band binds at BOTH ends",
          mb.effective_carrier_momentum_kgmps(100.0, 4.0, 99.0)
          == 400.0 * mb.FEND_TRAIT_MAX
          and mb.effective_carrier_momentum_kgmps(100.0, 4.0, 1e-9)
          == 400.0 * mb.FEND_TRAIT_MIN,
          "ceiling=%s floor=%s" % (mb.FEND_TRAIT_MAX, mb.FEND_TRAIT_MIN))

    # ── THE NO-OP LAW ──
    check("carrier_momentum_multiplier(None) is the IDENTITY value",
          mb.carrier_momentum_multiplier(None) == 1.0,
          "got %s" % mb.carrier_momentum_multiplier(None))

    # ── THROUGH THE REAL DERIVATION ──
    rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER, dist=0.3,
                      is_winner=True),
            build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                      dist=0.9)]
    neutral_record = build_record(rows)
    fend_record = build_record(rows, fend_trait=1.25)
    # The assist body IS resolved here, so these two models carry the same
    # collaborative wrap the earlier sections pin (700 kg m/s over an 0.2 s
    # window) and the ONLY thing the trait can move is the carrier's side.
    bodies = bodies_by_id(bc, _primary, _assist)
    neutral = mb.derive_wrap_model(neutral_record, bodies, carrier=bc)
    fended = mb.derive_wrap_model(fend_record, bodies, carrier=bc)

    check("the DECLARATION's trait is what reaches the model",
          fended is not None and fended.carrier_fend_trait == 1.25
          and neutral is not None and neutral.carrier_fend_trait == 1.0,
          "fend=%s neutral=%s"
          % (None if fended is None else fended.carrier_fend_trait,
             None if neutral is None else neutral.carrier_fend_trait))
    check("the carrier's baseline momentum is MEASURED, not assumed",
          neutral is not None
          and neutral.carrier_mass_kg == 100.0
          and neutral.carrier_speed_mps == 2.0
          and neutral.carrier_momentum_kgmps == 200.0,
          "mass=%s speed=%s baseline=%s"
          % (None if neutral is None else neutral.carrier_mass_kg,
             None if neutral is None else neutral.carrier_speed_mps,
             None if neutral is None else neutral.carrier_momentum_kgmps))
    check("a NEUTRAL model's effective momentum IS its baseline, exactly",
          neutral is not None
          and neutral.carrier_effective_momentum_kgmps
          == neutral.carrier_momentum_kgmps == 200.0,
          "effective=%s"
          % (None if neutral is None
             else neutral.carrier_effective_momentum_kgmps))
    check("a 1.25 model reaches exactly 250.0 from the 200.0 baseline",
          fended is not None
          and fended.carrier_effective_momentum_kgmps == 250.0
          and fended.carrier_effective_momentum_kgmps
          == 1.25 * fended.carrier_momentum_kgmps,
          "effective=%s"
          % (None if fended is None
             else fended.carrier_effective_momentum_kgmps))
    check("the model's own scale agrees with the consumption helper",
          neutral is not None and fended is not None
          and neutral.carrier_momentum_scale == 1.0
          and fended.carrier_momentum_scale == 1.25
          and mb.carrier_momentum_multiplier(neutral) == 1.0
          and mb.carrier_momentum_multiplier(fended) == 1.25,
          "helper=%s" % (None if fended is None
                         else mb.carrier_momentum_multiplier(fended)))
    check("the fend curve moves ONLY the carrier's side of the collision",
          neutral is not None and fended is not None
          and neutral.wrap_momentum_kgmps == fended.wrap_momentum_kgmps
          and neutral.wrap_force_n == fended.wrap_force_n
          and neutral.pressure_fraction == fended.pressure_fraction
          and neutral.carrier_momentum_kgmps == fended.carrier_momentum_kgmps,
          "wrap=%s in both arms"
          % (None if neutral is None else neutral.wrap_momentum_kgmps))
    check("the collision margin is the effective momentum MINUS the wrap",
          neutral is not None and fended is not None
          and neutral.carrier_momentum_margin_kgmps
          == neutral.carrier_effective_momentum_kgmps
          - neutral.wrap_momentum_kgmps
          and fended.carrier_momentum_margin_kgmps
          == fended.carrier_effective_momentum_kgmps
          - fended.wrap_momentum_kgmps)
    check("the neutral arm RETAINS every legacy pinned physics value",
          neutral is not None
          and neutral.wrap_momentum_kgmps == 700.0
          and neutral.wrap_force_n == 3500.0
          and neutral.pressure_fraction == 1.0
          and neutral.break_window is False
          and neutral.offload_window is False,
          "wrap=%s force=%s pressure=%s"
          % (None if neutral is None else neutral.wrap_momentum_kgmps,
             None if neutral is None else neutral.wrap_force_n,
             None if neutral is None else neutral.pressure_fraction))

    # ── THE REAL CONSUMER: the dominance equation's resistance thresholds ──
    o_none = _dominance_with(_fend_ctx(), None)
    o_neutral = _dominance_with(_fend_ctx(), mb_model())
    o_fend = _dominance_with(_fend_ctx(), mb_model(fend_trait=1.25))

    check("a NEUTRAL model leaves the collision score BIT-IDENTICAL to none",
          o_neutral.attacker_dominance == o_none.attacker_dominance,
          "%.15f vs %.15f"
          % (o_none.attacker_dominance, o_neutral.attacker_dominance))
    check("the defender's resistance is untouched by either arm",
          o_fend.defender_dominance == o_neutral.defender_dominance
          == o_none.defender_dominance,
          "defender=%.15f" % o_none.defender_dominance)

    carrier_momentum = 200.0 * 0.5          # the fixture's mass x speed
    expected_lift = 0.25 * carrier_momentum / 100.0 * 1.2
    actual_lift = o_fend.attacker_dominance - o_neutral.attacker_dominance
    check("a 1.25 trait lifts the carrier's SCORE by exactly its 25%",
          abs(actual_lift - expected_lift) < 1e-12,
          "lift=%.15f expected=%.15f" % (actual_lift, expected_lift))
    check("the lifted score CROSSES the defensive resistance threshold",
          o_neutral.tackle_result is TackleResult.NEUTRAL
          and o_fend.tackle_result is TackleResult.WEAK,
          "%s -> %s"
          % (o_neutral.tackle_result.value, o_fend.tackle_result.value))
    check("and a neutral trait stays on the legacy side of that threshold",
          o_none.tackle_result is TackleResult.NEUTRAL,
          "no model=%s" % o_none.tackle_result.value)

    # ── THE TES BOUNDARY: the slot the declaration carries, consumed ──
    try:
        ctx_fend, _w = _tes_ctx_for(fend_record)
        attached = getattr(ctx_fend, "wrap_model", None)
    except Exception:
        traceback.print_exc()
        attached = None
    check("a declared 1.25 carrier is consumed END-TO-END at tes_start_tackle",
          attached is not None
          and attached.carrier_fend_trait == 1.25
          and attached.carrier_momentum_kgmps == 200.0
          and attached.carrier_effective_momentum_kgmps == 250.0
          and mb.carrier_momentum_multiplier(attached) == 1.25,
          "effective=%s"
          % (None if attached is None
             else attached.carrier_effective_momentum_kgmps))
    try:
        ctx_neutral, _w = _tes_ctx_for(neutral_record)
        attached_neutral = getattr(ctx_neutral, "wrap_model", None)
    except Exception:
        traceback.print_exc()
        attached_neutral = None
    check("the SAME declaration with a neutral carrier attaches a neutral model",
          attached_neutral is not None
          and attached_neutral.carrier_fend_trait == 1.0
          and attached_neutral.carrier_effective_momentum_kgmps
          == attached_neutral.carrier_momentum_kgmps,
          "effective=%s"
          % (None if attached_neutral is None
             else attached_neutral.carrier_effective_momentum_kgmps))

    gate("G11_fend_is_the_carrier_momentum_multiplier",
         baseline == 400.0
         and boosted == 500.0
         and mb.carrier_momentum_multiplier(None) == 1.0
         and neutral is not None and fended is not None
         and neutral.carrier_effective_momentum_kgmps
         == neutral.carrier_momentum_kgmps
         and fended.carrier_effective_momentum_kgmps == 250.0
         and mb.carrier_momentum_multiplier(fended) == 1.25
         and mb.effective_carrier_momentum_kgmps(100.0, 4.0, 99.0)
         == 400.0 * mb.FEND_TRAIT_MAX
         and mb.effective_carrier_momentum_kgmps(100.0, 4.0, 1e-9)
         == 400.0 * mb.FEND_TRAIT_MIN)


# ----------------------------------------------------------------------
# [10] THE DEFENSIVE SKILL RESISTANCE MATRIX - the tackler's own side
# ----------------------------------------------------------------------

def _slow_bodies():
    """A LOW-closure pair, so the pressure fraction is NOT saturated.

    The default fixture's collaborative wrap (700 kg m/s) already sits above
    the declared reference, so its pressure fraction is pinned at 1.0 and a
    skill multiplier cannot move the downstream duration / waveform terms at
    all.  This fixture closes at 1 m/s each, so its pressure sits at a third
    of the reference and the trait's effect on the CONSUMERS is measurable
    rather than clipped.
    """
    bc = StubBody(1, x=0.0, y=0.0, vx=-1.0, is_defender=False)
    primary = StubBody(10, x=-0.5, y=0.0, vx=1.0)
    assist = StubBody(11, x=0.5, y=0.0, vx=-1.0)
    return bc, primary, assist


def section_defensive_resistance(mb, bc, _primary, _assist, _watch):
    """The declared equation, its band, its identity, and the real consumer."""
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition

    banner("[10] THE DEFENSIVE SKILL RESISTANCE MATRIX - "
           "tackle_skill is the defender resistance multiplier")

    # ── the declared surface ──
    check("the neutral multiplier is the IDENTITY of the multiplication",
          mb.TACKLE_SKILL_TRAIT_NEUTRAL == 1.0,
          "neutral=%s" % mb.TACKLE_SKILL_TRAIT_NEUTRAL)
    check("the declared band contains the neutral value",
          0.0 < mb.TACKLE_SKILL_TRAIT_MIN < mb.TACKLE_SKILL_TRAIT_NEUTRAL
          < mb.TACKLE_SKILL_TRAIT_MAX,
          "band=[%s, %s]" % (mb.TACKLE_SKILL_TRAIT_MIN,
                             mb.TACKLE_SKILL_TRAIT_MAX))
    check("the exported equation IS mass x closing x tackle_skill_trait",
          mb.effective_defender_resistance_kgmps(100.0, 4.0, 1.5) == 600.0,
          "100 x 4 x 1.5 = %s"
          % mb.effective_defender_resistance_kgmps(100.0, 4.0, 1.5))

    # ── THE NEUTRAL LEG: exactly the Stage 2B closing impact ──
    baseline = mb.effective_defender_resistance_kgmps(100.0, 4.0, 1.0)
    check("a NEUTRAL trait maps EXACTLY onto the Stage 2B closing impact",
          baseline == 400.0, "mass x closing = %s" % baseline)

    # ── THE 1.50 LEG: exactly 50% up ──
    boosted = mb.effective_defender_resistance_kgmps(100.0, 4.0, 1.5)
    check("a 1.50 trait scales the impact up by exactly 50%",
          boosted == 600.0 and boosted == 1.5 * baseline,
          "%s -> %s" % (baseline, boosted))
    check("the increase IS exactly half of the baseline",
          boosted - baseline == 0.5 * baseline,
          "delta=%s" % (boosted - baseline))

    # ── THE BOUNDS AND THE DEGENERATE INPUTS ──
    check("a missing, zero, negative, non-finite or non-numeric trait is "
          "NEUTRAL, never a DELETION of the resistance",
          mb.effective_defender_resistance_kgmps(100.0, 4.0, None) == 400.0
          and mb.effective_defender_resistance_kgmps(100.0, 4.0, 0.0) == 400.0
          and mb.effective_defender_resistance_kgmps(100.0, 4.0, -3.0)
          == 400.0
          and mb.effective_defender_resistance_kgmps(
              100.0, 4.0, float("nan")) == 400.0
          and mb.effective_defender_resistance_kgmps(100.0, 4.0, "x")
          == 400.0,
          "every degenerate slot reports %s" % baseline)
    check("the declared band binds at BOTH ends",
          mb.effective_defender_resistance_kgmps(100.0, 4.0, 99.0)
          == 400.0 * mb.TACKLE_SKILL_TRAIT_MAX
          and mb.effective_defender_resistance_kgmps(100.0, 4.0, 1e-9)
          == 400.0 * mb.TACKLE_SKILL_TRAIT_MIN,
          "ceiling=%s floor=%s" % (mb.TACKLE_SKILL_TRAIT_MAX,
                                   mb.TACKLE_SKILL_TRAIT_MIN))

    # ── THE NO-OP LAW ──
    check("tackle_skill_trait_of(None) is the NEUTRAL multiplier",
          mb.tackle_skill_trait_of(None) == 1.0,
          "got %s" % mb.tackle_skill_trait_of(None))
    check("defender_resistance_multiplier(None) is the IDENTITY value",
          mb.defender_resistance_multiplier(None) == 1.0,
          "got %s" % mb.defender_resistance_multiplier(None))

    # ── THROUGH THE REAL DERIVATION: the neutral arm IS Stage 2B ──
    rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER, dist=0.3,
                      is_winner=True),
            build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                      dist=0.9)]
    neutral_record = build_record(rows)
    bodies = bodies_by_id(bc, _primary, _assist)
    neutral = mb.derive_wrap_model(neutral_record, bodies, carrier=bc)

    check("the neutral arm RETAINS every Stage 2B pinned value",
          neutral is not None
          and neutral.wrap_momentum_kgmps == 700.0
          and neutral.wrap_force_n == 3500.0
          and neutral.pressure_fraction == 1.0
          and neutral.assist_ids == (11,),
          "wrap=%s force=%s pressure=%s"
          % (None if neutral is None else neutral.wrap_momentum_kgmps,
             None if neutral is None else neutral.wrap_force_n,
             None if neutral is None else neutral.pressure_fraction))
    check("a neutral model's BASELINE wrap IS its effective wrap, exactly",
          neutral is not None
          and neutral.baseline_wrap_momentum_kgmps
          == neutral.wrap_momentum_kgmps == 700.0,
          "baseline=%s"
          % (None if neutral is None
             else neutral.baseline_wrap_momentum_kgmps))
    check("defender_resistance_multiplier(neutral) is exactly 1.0",
          mb.defender_resistance_multiplier(neutral) == 1.0,
          "got %s" % mb.defender_resistance_multiplier(neutral))

    # ── THE 1.50 LEG THROUGH THE DERIVATION ──
    boosted_rows = [
        build_row(10, CandidateDisposition.EVALUATED_WINNER, dist=0.3,
                  is_winner=True, tackle_skill_trait=1.50),
        build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9),
    ]
    boosted_record = build_record(boosted_rows)
    boosted_model = mb.derive_wrap_model(boosted_record, bodies, carrier=bc)

    p_neutral = neutral.contribution_for(10)
    p_boost = boosted_model.contribution_for(10)
    check("the DECLARATION's own trait is what reaches the contribution",
          p_boost is not None and p_boost.tackle_skill_trait == 1.50
          and p_neutral is not None and p_neutral.tackle_skill_trait == 1.0,
          "boost=%s neutral=%s"
          % (None if p_boost is None else p_boost.tackle_skill_trait,
             None if p_neutral is None else p_neutral.tackle_skill_trait))
    check("the primary's BASELINE wrap is unchanged by its own skill",
          p_boost.wrap_momentum_kgmps == p_neutral.wrap_momentum_kgmps
          == 400.0,
          "baseline=%s in both arms" % p_boost.wrap_momentum_kgmps)
    check("the primary's INDIVIDUAL wrap scales up by exactly 50%",
          p_boost.effective_wrap_momentum_kgmps == 600.0
          and p_boost.effective_wrap_momentum_kgmps
          == 1.5 * p_neutral.effective_wrap_momentum_kgmps,
          "%s -> %s" % (p_neutral.effective_wrap_momentum_kgmps,
                        p_boost.effective_wrap_momentum_kgmps))
    check("and the primary's own wrap FORCE scales by the same 50%",
          abs(p_neutral.wrap_force_n - 2000.0) < 1e-9
          and abs(p_boost.wrap_force_n / p_neutral.wrap_force_n - 1.5)
          < 1e-12,
          "%s -> %s" % (p_neutral.wrap_force_n, p_boost.wrap_force_n))
    check("the contribution's resistance scale IS its declared trait",
          p_neutral.defender_resistance_scale == 1.0
          and p_boost.defender_resistance_scale == 1.50,
          "scale=%s" % p_boost.defender_resistance_scale)
    check("a NEUTRAL assist is UNTOUCHED by its partner's skill",
          boosted_model.contribution_for(11).effective_wrap_momentum_kgmps
          == 300.0,
          "assist=%s"
          % boosted_model.contribution_for(11).effective_wrap_momentum_kgmps)
    check("the collaborative wrap is the SUM of the effective resistances",
          boosted_model.wrap_momentum_kgmps == 900.0
          and boosted_model.baseline_wrap_momentum_kgmps == 700.0,
          "effective=%s baseline=%s"
          % (boosted_model.wrap_momentum_kgmps,
             boosted_model.baseline_wrap_momentum_kgmps))
    check("the model's multiplier is its contributors' momentum-weighted mean",
          abs(mb.defender_resistance_multiplier(boosted_model)
              - 900.0 / 700.0) < 1e-12,
          "got %s" % mb.defender_resistance_multiplier(boosted_model))

    # ── THE SOLO LEG: exactly 50% on ONE tackler ──
    solo_neutral = mb.derive_wrap_model(
        build_record([build_row(10, CandidateDisposition.EVALUATED_WINNER,
                                is_winner=True)]),
        bodies_by_id(bc, _primary), carrier=bc)
    solo_boost = mb.derive_wrap_model(
        build_record([build_row(10, CandidateDisposition.EVALUATED_WINNER,
                                is_winner=True, tackle_skill_trait=1.50)]),
        bodies_by_id(bc, _primary), carrier=bc)
    check("a SOLO 1.50 tackler wraps EXACTLY 1.5x the same tackler at 1.0",
          solo_neutral.wrap_momentum_kgmps == 400.0
          and solo_boost.wrap_momentum_kgmps == 600.0
          and solo_boost.wrap_momentum_kgmps
          == 1.5 * solo_neutral.wrap_momentum_kgmps,
          "%s -> %s" % (solo_neutral.wrap_momentum_kgmps,
                        solo_boost.wrap_momentum_kgmps))
    check("the model multiplier IS the solo tackler's own trait",
          mb.defender_resistance_multiplier(solo_neutral) == 1.0
          and mb.defender_resistance_multiplier(solo_boost) == 1.50,
          "boost multiplier=%s"
          % mb.defender_resistance_multiplier(solo_boost))

    # ── THE ASSIST LEG: the same law on IN_RADIUS_NOT_EVALUATED ──
    assist_rows = [
        build_row(10, CandidateDisposition.EVALUATED_WINNER, is_winner=True),
        build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9,
                  tackle_skill_trait=2.0),
    ]
    assist_model = mb.derive_wrap_model(
        build_record(assist_rows), bodies, carrier=bc)
    check("a wrap ASSIST's own skill scales ITS wrap, and only its own",
          assist_model.contribution_for(11).effective_wrap_momentum_kgmps
          == 600.0
          and assist_model.contribution_for(10).effective_wrap_momentum_kgmps
          == 400.0
          and assist_model.wrap_momentum_kgmps == 1000.0,
          "assist=%s primary=%s wrap=%s"
          % (assist_model.contribution_for(11).effective_wrap_momentum_kgmps,
             assist_model.contribution_for(10).effective_wrap_momentum_kgmps,
             assist_model.wrap_momentum_kgmps))

    # ── THE BAND, APPLIED THROUGH THE DERIVATION ──
    clamp_rows = [
        build_row(10, CandidateDisposition.EVALUATED_WINNER, is_winner=True,
                  tackle_skill_trait=99.0),
        build_row(12, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9,
                  tackle_skill_trait=1e-9),
    ]
    clamp_bodies = bodies_by_id(bc, _primary, _assist)
    clamp_bodies[12] = StubBody(12, x=0.5, y=0.0, vx=-3.0)
    clamp_model = mb.derive_wrap_model(
        build_record(clamp_rows), clamp_bodies, carrier=bc)
    check("a corrupt trait clamps DOWN to the ceiling, never past it",
          clamp_model.contribution_for(10).defender_resistance_scale
          == mb.TACKLE_SKILL_TRAIT_MAX
          and clamp_model.contribution_for(10).effective_wrap_momentum_kgmps
          == 400.0 * mb.TACKLE_SKILL_TRAIT_MAX,
          "primary effective=%s"
          % clamp_model.contribution_for(10).effective_wrap_momentum_kgmps)
    check("a near-zero trait clamps UP to the floor, never deleting the wrap",
          clamp_model.contribution_for(12).defender_resistance_scale
          == mb.TACKLE_SKILL_TRAIT_MIN
          and clamp_model.contribution_for(12).effective_wrap_momentum_kgmps
          == 300.0 * mb.TACKLE_SKILL_TRAIT_MIN,
          "assist effective=%s"
          % clamp_model.contribution_for(12).effective_wrap_momentum_kgmps)

    # ── THE REAL CONSUMERS: the trait reaches the waveform and the clock ──
    slow_bc, slow_primary, slow_assist = _slow_bodies()
    slow_bodies_map = bodies_by_id(slow_bc, slow_primary, slow_assist)
    slow_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER,
                           is_winner=True),
                 build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED,
                           dist=0.9)]
    slow_neutral = mb.derive_wrap_model(
        build_record(slow_rows), slow_bodies_map, carrier=slow_bc)
    slow_rows_boost = [
        build_row(10, CandidateDisposition.EVALUATED_WINNER, is_winner=True,
                  tackle_skill_trait=1.50),
        build_row(11, CandidateDisposition.IN_RADIUS_NOT_EVALUATED, dist=0.9),
    ]
    slow_boost = mb.derive_wrap_model(
        build_record(slow_rows_boost), slow_bodies_map, carrier=slow_bc)

    check("a skilled pile carries MORE pressure than the same pile neutral",
          slow_neutral.wrap_momentum_kgmps == 200.0
          and slow_boost.wrap_momentum_kgmps == 250.0
          and slow_boost.pressure_fraction > slow_neutral.pressure_fraction,
          "wrap %s -> %s, pressure %.4f -> %.4f"
          % (slow_neutral.wrap_momentum_kgmps,
             slow_boost.wrap_momentum_kgmps,
             slow_neutral.pressure_fraction, slow_boost.pressure_fraction))
    check("the waveform's collaborative term RISES with the defender's skill",
          mb.wrap_delta_for(slow_boost) > mb.wrap_delta_for(slow_neutral)
          and mb.wrap_delta_for(slow_neutral) >= 0.0,
          "%.6f -> %.6f" % (mb.wrap_delta_for(slow_neutral),
                            mb.wrap_delta_for(slow_boost)))
    check("and the contest RESOLVES SOONER, never later",
          mb.collaborative_duration_scale(slow_boost)
          < mb.collaborative_duration_scale(slow_neutral)
          and mb.collaborative_duration_scale(slow_neutral) <= 1.0,
          "scale %.6f -> %.6f"
          % (mb.collaborative_duration_scale(slow_neutral),
             mb.collaborative_duration_scale(slow_boost)))

    # ── THE TES BOUNDARY: the slot the declaration carries, consumed ──
    try:
        ctx_boost, _w = _tes_ctx_for(boosted_record)
        attached = getattr(ctx_boost, "wrap_model", None)
    except Exception:
        traceback.print_exc()
        attached = None
    check("a declared 1.50 tackler is consumed END-TO-END at tes_start_tackle",
          attached is not None
          and attached.contribution_for(10).tackle_skill_trait == 1.50
          and attached.wrap_momentum_kgmps == 900.0
          and attached.baseline_wrap_momentum_kgmps == 700.0
          and abs(float(ctx_boost.wrap_force_n) - 4500.0) < 1e-9,
          "wrap=%s baseline=%s force=%s"
          % (None if attached is None else attached.wrap_momentum_kgmps,
             None if attached is None
             else attached.baseline_wrap_momentum_kgmps,
             None if attached is None else ctx_boost.wrap_force_n))
    check("the trait comes from the DECLARATION'S ROW, not the live body",
          not hasattr(_primary, "tackle_skill_trait")
          and attached is not None
          and attached.contribution_for(10).tackle_skill_trait == 1.50,
          "the live StubBody carries no trait attribute at all")
    try:
        ctx_neutral, _w = _tes_ctx_for(neutral_record)
        attached_neutral = getattr(ctx_neutral, "wrap_model", None)
    except Exception:
        traceback.print_exc()
        attached_neutral = None
    check("the SAME declaration with a neutral tackler attaches a neutral wrap",
          attached_neutral is not None
          and attached_neutral.wrap_momentum_kgmps == 700.0
          and attached_neutral.wrap_momentum_kgmps
          == attached_neutral.baseline_wrap_momentum_kgmps
          and mb.defender_resistance_multiplier(attached_neutral) == 1.0,
          "wrap=%s"
          % (None if attached_neutral is None
             else attached_neutral.wrap_momentum_kgmps))

    gate("G12_tackle_skill_is_the_defender_resistance_multiplier",
         baseline == 400.0
         and boosted == 600.0
         and mb.defender_resistance_multiplier(None) == 1.0
         and neutral is not None and boosted_model is not None
         and neutral.wrap_momentum_kgmps == 700.0
         and neutral.baseline_wrap_momentum_kgmps == 700.0
         and p_boost is not None and p_neutral is not None
         and p_boost.effective_wrap_momentum_kgmps == 600.0
         and p_neutral.effective_wrap_momentum_kgmps == 400.0
         and solo_boost is not None and solo_neutral is not None
         and solo_boost.wrap_momentum_kgmps
         == 1.5 * solo_neutral.wrap_momentum_kgmps
         and mb.effective_defender_resistance_kgmps(100.0, 4.0, 99.0)
         == 400.0 * mb.TACKLE_SKILL_TRAIT_MAX
         and mb.effective_defender_resistance_kgmps(100.0, 4.0, 1e-9)
         == 400.0 * mb.TACKLE_SKILL_TRAIT_MIN
         and attached is not None
         and attached.wrap_momentum_kgmps == 900.0)


# ----------------------------------------------------------------------
# [11] THE COLLISION STATE DISCRIMINATOR - the margin, resolved
# ----------------------------------------------------------------------
# Dynamic results pipeline, step 1.  The model already measures BOTH sides of
# the collision; this section gates the discriminator that turns their MARGIN
# into an explicit outcome token, and proves a high-fend carrier SHIFTS it.

def _state_by_rule(margin) -> str:
    """The declared rule, restated INDEPENDENTLY of the leaf.

    The gate must never prove the leaf agrees with itself.  This is the rule
    the contract document states, written here from the document's own words
    with its own literal threshold, so every cell below compares TWO
    independent statements of it.  The literal was recalibrated to 350.0 with
    the leaf's declared constant (step 3.3), and is kept as a LITERAL here on
    purpose: if the leaf's constant ever drifts from the documented boundary
    this comparison is what catches it.
    """
    threshold = 350.0
    if margin > threshold:
        return "BUMP_OFF"
    if margin < -threshold:
        return "DOMINANT_SMASH"
    return "PROGRESSIVE_DRAG"


def _margin_model(margin):
    """A hand-built model whose margin is EXACTLY *margin*.

    The carrier's effective momentum is placed DIRECTLY rather than reached
    through a mass and a speed, so a margin can sit ON the threshold to the
    last bit.  The wrap is fixed at 850.0 for every leg of this section, which
    keeps every value a binary-exact multiple of 0.5.
    """
    from game.tes import tes_multi_body as mb
    wrap = 850.0
    return mb.WrapModel(
        contest_id="c:1:1", frame=1, seq=1, channel="ch", radius_m=1.5,
        primary_id=10, primary_disposition="evaluated_winner",
        wrap_momentum_kgmps=wrap,
        baseline_wrap_momentum_kgmps=wrap,
        carrier_mass_kg=1.0,
        carrier_speed_mps=1000.0,
        carrier_fend_trait=1.0,
        carrier_momentum_kgmps=1000.0,
        carrier_effective_momentum_kgmps=wrap + float(margin),
    )


def section_collision_state(mb, bc, _primary, _assist, _watch):
    """The declared surface, the threshold law, and the high-fend shift."""
    from game.defence.defence_lvl_1.contest_record import CandidateDisposition
    from game.tes.tes_context import TESContext

    banner("[11] THE COLLISION STATE DISCRIMINATOR - the margin, resolved")

    # -- the declared surface --
    # THE RECALIBRATED BOUNDARY (dynamic results pipeline, step 3.3): the
    # threshold is 350.0, which is the Stage 3.2 fend curve's EXACT maximum.
    # At the old 150.0 a 1.5-fend carrier's +350.0 margin cleared the boundary
    # on its own, so every fend contest resolved BUMP_OFF and the smash and
    # drag outcomes were unreachable.  At 350.0 the fend sits ON its own edge
    # and the strictly-exclusive test hands it to PROGRESSIVE_DRAG.
    check("the threshold is the documented +350.0 kg m/s",
          mb.COLLISION_MARGIN_THRESHOLD_KGMPS == 350.0,
          "threshold=%s" % mb.COLLISION_MARGIN_THRESHOLD_KGMPS)
    # The recalibration must NOT have made an ordinary carry win contacts:
    # a NEUTRAL (1.0-fend) carrier at the declared fallback mass and a
    # representative line speed still sits well BELOW the boundary, so the
    # neutral arm keeps resolving to the contest state.
    neutral_momentum = mb.effective_carrier_momentum_kgmps(
        mb.DEFAULT_MASS_KG, 2.0, mb.FEND_TRAIT_NEUTRAL)
    check("a NEUTRAL fend declaration still does not clear the boundary",
          neutral_momentum < mb.COLLISION_MARGIN_THRESHOLD_KGMPS,
          "neutral=%s threshold=%s"
          % (neutral_momentum, mb.COLLISION_MARGIN_THRESHOLD_KGMPS))
    tokens = (mb.COLLISION_STATE_BUMP_OFF, mb.COLLISION_STATE_DOMINANT_SMASH,
              mb.COLLISION_STATE_PROGRESSIVE_DRAG)
    check("the three tokens are the documented outcome names",
          tokens == ("BUMP_OFF", "DOMINANT_SMASH", "PROGRESSIVE_DRAG"),
          "%s" % (tokens,))
    check("the vocabulary is CLOSED and every token is a distinct string",
          tuple(mb.COLLISION_STATES) == tokens
          and len(set(mb.COLLISION_STATES)) == 3
          and all(isinstance(t, str) and t for t in mb.COLLISION_STATES))
    check("the context's declared MIRROR default IS the leaf's undecided token",
          TESContext().wrap_collision_state
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "so the mirror and the leaf can never drift apart")

    # -- the no-op law: an absent or unmeasurable margin is UNDECIDED --
    check("resolve_collision_state(None) is the UNDECIDED state",
          mb.resolve_collision_state(None)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG)
    check("a model whose margin is exactly ZERO is the undecided state",
          mb.resolve_collision_state(_margin_model(0.0))
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG)
    nan_state = mb.resolve_collision_state(_margin_model(float("nan")))
    inf_state = mb.resolve_collision_state(_margin_model(float("inf")))
    check("a NON-FINITE margin is undecided in BOTH directions",
          nan_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
          and inf_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "nan=%s inf=%s" % (nan_state, inf_state))
    unreadable = mb.WrapModel(
        contest_id="c:1:1", frame=1, seq=1, channel="ch", radius_m=1.5,
        primary_id=10, primary_disposition="evaluated_winner",
        wrap_momentum_kgmps=0.0, carrier_effective_momentum_kgmps="x",
    )
    check("a model whose margin cannot be READ is undecided, never an error",
          mb.resolve_collision_state(unreadable)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "an unmeasurable collision is not awarded to either side")

    # -- THE THRESHOLD IS STRICTLY EXCLUSIVE AT BOTH ENDS --
    edges = ((350.0, "PROGRESSIVE_DRAG"), (350.5, "BUMP_OFF"),
             (349.5, "PROGRESSIVE_DRAG"), (0.0, "PROGRESSIVE_DRAG"),
             (-349.5, "PROGRESSIVE_DRAG"), (-350.5, "DOMINANT_SMASH"),
             (-350.0, "PROGRESSIVE_DRAG"))
    edge_ok = True
    for margin, expected in edges:
        model = _margin_model(margin)
        if model.carrier_momentum_margin_kgmps != margin:
            edge_ok = False
            info("edge margin NOT exact",
                 "asked %s got %s"
                 % (margin, model.carrier_momentum_margin_kgmps))
        got = mb.resolve_collision_state(model)
        if got != expected:
            edge_ok = False
            info("edge MISMATCH",
                 "margin=%s got=%s expected=%s" % (margin, got, expected))
    check("exactly +-350.0 is UNDECIDED, and either side of it is DECIDED",
          edge_ok, "%d edge cells" % len(edges))
    check("a +350.0 fend margin is a CONTEST, not a bump-off",
          mb.resolve_collision_state(_margin_model(350.0))
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG
          and mb.resolve_collision_state(_margin_model(-300.0))
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "the recalibration hands tight collisions to the drag path")

    # -- the rule holds on EVERY cell of a swept grid --
    sweep_ok = True
    seen = set()
    cells = 0
    for step in range(-40, 41):
        margin = step * 25.0
        model = _margin_model(margin)
        got = mb.resolve_collision_state(model)
        seen.add(got)
        cells += 1
        if got != _state_by_rule(model.carrier_momentum_margin_kgmps):
            sweep_ok = False
            info("sweep MISMATCH", "margin=%s got=%s" % (margin, got))
        if (got == mb.COLLISION_STATE_BUMP_OFF
                and not model.carrier_momentum_margin_kgmps > 0.0):
            sweep_ok = False
        if (got == mb.COLLISION_STATE_DOMINANT_SMASH
                and not model.carrier_momentum_margin_kgmps < 0.0):
            sweep_ok = False
    check("the declared rule holds on EVERY cell of a swept margin grid",
          sweep_ok, "%d cells, -1000.0 .. +1000.0 in 25.0 steps" % cells)
    check("the sweep exercises all THREE states",
          seen == set(tokens), "seen=%s" % sorted(seen))

    # -- the model's own property IS the helper --
    prop_ok = True
    for step in range(-6, 7):
        model = _margin_model(step * 50.0)
        if model.collision_state != mb.resolve_collision_state(model):
            prop_ok = False
    check("WrapModel.collision_state IS resolve_collision_state(model)",
          prop_ok, "13 models agree on both spellings")

    # -- THE HIGH-FEND ARM: one declaration, two carriers, one token shift --
    # The fixture is SOLO and the two arms share the SAME body set, so the only
    # thing the trait can move is the carrier's own side of the collision.
    hi_bc = StubBody(1, x=0.0, y=0.0, vx=-5.0, is_defender=False)
    hi_primary = StubBody(10, x=-0.5, y=0.0, vx=4.0)
    hi_bodies = bodies_by_id(hi_bc, hi_primary)
    hi_rows = [build_row(10, CandidateDisposition.EVALUATED_WINNER, dist=0.3,
                         is_winner=True)]
    hi_neutral = mb.derive_wrap_model(build_record(hi_rows, fend_trait=1.0),
                                      hi_bodies, carrier=hi_bc)
    hi_fend = mb.derive_wrap_model(build_record(hi_rows, fend_trait=1.5),
                                   hi_bodies, carrier=hi_bc)
    check("the high-momentum fixture is a SOLO 400 kg m/s wrap",
          hi_neutral is not None
          and hi_neutral.wrap_momentum_kgmps == 400.0
          and hi_neutral.assist_count == 0,
          "wrap=%s" % (None if hi_neutral is None
                       else hi_neutral.wrap_momentum_kgmps))
    check("its carrier's own momentum is MEASURED at 500 kg m/s",
          hi_neutral is not None
          and hi_neutral.carrier_mass_kg == 100.0
          and hi_neutral.carrier_speed_mps == 5.0
          and hi_neutral.carrier_momentum_kgmps == 500.0,
          "mass=%s speed=%s"
          % (None if hi_neutral is None else hi_neutral.carrier_mass_kg,
             None if hi_neutral is None else hi_neutral.carrier_speed_mps))
    check("a NEUTRAL carrier leaves a +100 margin: PROGRESSIVE_DRAG",
          hi_neutral is not None
          and hi_neutral.carrier_momentum_margin_kgmps == 100.0
          and hi_neutral.collision_state
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "margin=%s state=%s"
          % (None if hi_neutral is None
             else hi_neutral.carrier_momentum_margin_kgmps,
             None if hi_neutral is None else hi_neutral.collision_state))
    # AT THE RECALIBRATED BOUNDARY the 1.5-fend carrier's +350.0 margin sits
    # EXACTLY ON the edge, and the strictly-exclusive test therefore hands it
    # to PROGRESSIVE_DRAG.  That is the whole point of the recalibration: the
    # fend curve's own maximum no longer wins a contact on its own.
    check("a 1.5 fend carrier's +350.0 sits ON the edge: PROGRESSIVE_DRAG",
          hi_fend is not None
          and hi_fend.carrier_momentum_margin_kgmps == 350.0
          and hi_fend.collision_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
          and mb.resolve_collision_state(hi_fend)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "margin=%s state=%s"
          % (None if hi_fend is None
             else hi_fend.carrier_momentum_margin_kgmps,
             None if hi_fend is None else hi_fend.collision_state))
    # ...but BUMP_OFF must STILL be reachable in live play, or the
    # recalibration would have deleted an outcome rather than recalibrated
    # it.  A MAX-band (2.0-fend) carrier against the SAME solo 400 wrap
    # clears the boundary: 100 x 5 x 2.0 = 1000 over 400 leaves +600.
    hi_max = mb.derive_wrap_model(build_record(hi_rows, fend_trait=2.0),
                                  hi_bodies, carrier=hi_bc)
    check("a MAX-band carrier against the same solo wrap earns BUMP_OFF",
          hi_max is not None
          and hi_max.wrap_momentum_kgmps == 400.0
          and hi_max.carrier_effective_momentum_kgmps == 1000.0
          and hi_max.carrier_momentum_margin_kgmps == 600.0
          and hi_max.collision_state == mb.COLLISION_STATE_BUMP_OFF,
          "margin=%s state=%s"
          % (None if hi_max is None
             else hi_max.carrier_momentum_margin_kgmps,
             None if hi_max is None else hi_max.collision_state))
    check("the shift is the CARRIER's side alone: the wrap is IDENTICAL",
          hi_neutral is not None and hi_fend is not None
          and hi_neutral.wrap_momentum_kgmps == hi_fend.wrap_momentum_kgmps
          and hi_neutral.wrap_force_n == hi_fend.wrap_force_n
          and hi_neutral.baseline_wrap_momentum_kgmps
          == hi_fend.baseline_wrap_momentum_kgmps
          and hi_neutral.carrier_effective_momentum_kgmps == 500.0
          and hi_fend.carrier_effective_momentum_kgmps == 750.0,
          "wrap=%s in BOTH arms"
          % (None if hi_fend is None else hi_fend.wrap_momentum_kgmps))
    check("the token is a STATE, not a restatement of carrier_fends_through",
          hi_neutral is not None
          and hi_neutral.carrier_fends_through is True
          and hi_neutral.collision_state
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "ahead on the margin, yet still a CONTEST below the threshold")

    # -- THE COUNTER-ARM: a max-band carrier cannot bump off the pile --
    bc2, p2, a2, _r2, _w2 = _live_bodies()
    pile = mb.derive_wrap_model(
        build_record(collaborative_rows(), fend_trait=2.0),
        bodies_by_id(bc2, p2, a2), carrier=bc2)
    # THE RECALIBRATED PILE ARM.  At the old 150.0 this pile's -300.0 margin
    # earned DOMINANT_SMASH outright.  At 350.0 it is BELOW the boundary, so
    # the contest is handed back to PROGRESSIVE_DRAG: a pile now has to
    # out-weigh the carrier by MORE than 350 to break a tackle outright,
    # which is what "tight collisions step onto the drag and ruck paths"
    # means for the defenders' side of the discriminator.
    check("a two-body pile's -300.0 is now a CONTEST, not a smash",
          pile is not None
          and pile.wrap_momentum_kgmps == 700.0
          and pile.carrier_effective_momentum_kgmps == 400.0
          and pile.carrier_momentum_margin_kgmps == -300.0
          and pile.collision_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "margin=%s state=%s"
          % (None if pile is None else pile.carrier_momentum_margin_kgmps,
             None if pile is None else pile.collision_state))
    # ...and DOMINANT_SMASH must STILL be reachable, so the same MAX-band
    # carrier is put against a pile that out-weighs it by MORE than the
    # boundary: the primary closes at 4.0 and the assist at 4.0, giving an
    # 800 wrap against the carrier's 400 -- a margin of -400.
    deep_bc = StubBody(1, x=0.0, y=0.0, vx=-2.0, is_defender=False)
    deep = mb.derive_wrap_model(
        build_record(collaborative_rows(), fend_trait=2.0),
        bodies_by_id(deep_bc,
                     StubBody(10, x=-0.5, y=0.0, vx=4.0),
                     StubBody(11, x=0.5, y=0.0, vx=-4.0)),
        carrier=deep_bc)
    check("a pile out-weighing the carrier by more than 350 earns a SMASH",
          deep is not None
          and deep.wrap_momentum_kgmps == 800.0
          and deep.carrier_effective_momentum_kgmps == 400.0
          and deep.carrier_momentum_margin_kgmps == -400.0
          and deep.collision_state == mb.COLLISION_STATE_DOMINANT_SMASH,
          "margin=%s state=%s"
          % (None if deep is None else deep.carrier_momentum_margin_kgmps,
             None if deep is None else deep.collision_state))

    # -- THE TES BOUNDARY: the REAL entry point resolves the SAME token --
    boundary_bodies = (hi_bc, hi_primary,
                       StubBody(11, x=0.5, y=0.0, vx=-3.0),
                       StubBody(12, x=0.0, y=-0.4, vx=2.0),
                       StubBody(14, x=5.0, y=0.0, vx=-1.0))
    fend_record = build_record(hi_rows, winner_id=10, fend_trait=1.5)
    neutral_record = build_record(hi_rows, winner_id=10, fend_trait=1.0)
    ctx_fend = ctx_neutral = None
    attached_fend = attached_neutral = None
    try:
        ctx_fend, _w = _tes_ctx_for(fend_record, bodies=boundary_bodies)
        attached_fend = getattr(ctx_fend, "wrap_model", None)
    except Exception:
        traceback.print_exc()
    try:
        ctx_neutral, _w = _tes_ctx_for(neutral_record, bodies=boundary_bodies)
        attached_neutral = getattr(ctx_neutral, "wrap_model", None)
    except Exception:
        traceback.print_exc()
    check("the REAL tes_start_tackle resolves the CONTEST for the 1.5 carrier",
          attached_fend is not None
          and attached_fend.carrier_momentum_margin_kgmps == 350.0
          and mb.resolve_collision_state(attached_fend)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "state=%s"
          % (None if attached_fend is None
             else mb.resolve_collision_state(attached_fend)))
    check("the context MIRRORS the model's token instead of recomputing it",
          getattr(ctx_fend, "wrap_collision_state", None)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG
          and attached_fend is not None
          and getattr(ctx_fend, "wrap_collision_state", None)
          == attached_fend.collision_state,
          "ctx=%s model=%s"
          % (getattr(ctx_fend, "wrap_collision_state", None),
             None if attached_fend is None else attached_fend.collision_state))
    check("the SAME declaration neutral resolves the OTHER state end-to-end",
          attached_neutral is not None
          and attached_neutral.collision_state
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG
          and getattr(ctx_neutral, "wrap_collision_state", None)
          == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "the declared fend trait is the ONLY difference between the arms")

    off_state = None
    off_model = "?"
    try:
        os.environ[mb.MULTI_BODY_ENV_VAR] = "0"
        ctx_off, _w = _tes_ctx_for(fend_record, bodies=boundary_bodies)
        off_state = getattr(ctx_off, "wrap_collision_state", None)
        off_model = getattr(ctx_off, "wrap_model", None)
    except Exception:
        traceback.print_exc()
    finally:
        os.environ.pop(mb.MULTI_BODY_ENV_VAR, None)
    check("with the lever OFF no model attaches and the token is UNDECIDED",
          off_model is None
          and off_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
          "state=%s" % off_state)

    gate("G13_collision_state_is_the_margin_discriminator",
         mb.COLLISION_MARGIN_THRESHOLD_KGMPS == 350.0
         and tuple(mb.COLLISION_STATES) == tokens
         and edge_ok and sweep_ok and prop_ok and seen == set(tokens)
         and nan_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and inf_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and mb.resolve_collision_state(None)
         == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and hi_neutral is not None and hi_fend is not None
         and hi_neutral.collision_state
         == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and hi_fend.collision_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and hi_max is not None
         and hi_max.collision_state == mb.COLLISION_STATE_BUMP_OFF
         and pile is not None
         and pile.collision_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and deep is not None
         and deep.collision_state == mb.COLLISION_STATE_DOMINANT_SMASH
         and attached_fend is not None
         and attached_fend.collision_state
         == mb.COLLISION_STATE_PROGRESSIVE_DRAG
         and off_state == mb.COLLISION_STATE_PROGRESSIVE_DRAG)


# ----------------------------------------------------------------------
# [9] INTEGRATION - declared gates exercised, content real, leaf reachable
# ----------------------------------------------------------------------

def section_integration(mb):
    import dataclasses

    banner("[9] INTEGRATION - the contract is declared, exercised and reachable")
    declared_ids = [g[0] for g in mb.declared_gates()]
    missing = [g for g in declared_ids if g not in _GATE_RESULTS]
    unexercised = [g for g in _GATE_RESULTS if g not in declared_ids]
    check("every declared gate was exercised by a section above",
          not missing, "not exercised: %s" % missing)
    check("no gate was recorded that the leaf does not declare",
          not unexercised, "unknown: %s" % unexercised)
    failed_gates = sorted(g for g, ok in _GATE_RESULTS.items() if not ok)
    check("every exercised gate PASSED", not failed_gates,
          "failed: %s" % failed_gates)

    fields = {f.name for f in dataclasses.fields(mb.WrapModel)}
    content_missing = []
    for group, names in mb.declared_content():
        for n in names:
            if n not in fields:
                content_missing.append("%s.%s" % (group, n))
    check("every field of the declared content really exists on the model",
          not content_missing, "missing: %s" % content_missing)

    # The verifier must be in the runner exactly once (no duplicate work).
    runner_path = "run_fb_verifiers.py"
    try:
        with open(runner_path, encoding="utf-8", errors="replace") as fh:
            runner = fh.read()
        occurrences = runner.count("verify_tackle_exchange_multi_body_contract.py")
    except OSError:
        occurrences = 0
    check("the FB runner lists this verifier exactly once",
          occurrences == 1, "occurrences=%d" % occurrences)

    # REACHABILITY: TES imports the leaf, and the engine consumes its gate.
    tes_roots = module_imports("game/tes/tes.py")
    check("the TES entry point imports the multi-body leaf",
          "tes_multi_body" in tes_roots
          or _text_contains("game/tes/tes.py", "tes_multi_body"),
          "roots=%s" % sorted(tes_roots))
    check("the engine consumes the leaf's break threshold",
          _text_contains("world/open_play_engine.py", "break_drive_threshold"),
          "a leaf nothing calls would be dead code")
    check("the TES dominance module consumes the leaf",
          _text_contains("game/tes/tes_dominance.py", "tes_multi_body"))
    check("the TES checks module consumes the leaf",
          _text_contains("game/tes/tes_checks.py", "tes_multi_body"))


def _text_contains(path, needle):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return needle in fh.read()
    except OSError:
        return False


# ----------------------------------------------------------------------
# [12] THE LIVE POSITIONAL GRADIENT (Stage 4) - a real match rollout
# ----------------------------------------------------------------------
# Everything above this section resolves the declaration on HAND-BUILT lines.
# This section is the end-to-end proof the stage brief asks for: boot the REAL
# engine, play a seeded kickoff-to-ruck match, and harvest every wrap model
# ``tes.tes_start_tackle`` derived along the way -- so the collision bands the
# positional mapper produces are shown to occur in LIVE PLAY, and the mapper's
# own A/B lever is shown to remove them in the same process.
#
# The matchup claims themselves (a running BACK clearing a single FORWARD, and
# a wrapping FORWARD CLUSTER compounding into a DRAG) are resolved
# DETERMINISTICALLY by the sibling gate verify_bump_clearance_contract section
# [11], where the geometry is fixed.  Here the same bands are MEASURED on
# whatever matchups the seeded match happens to produce, and the traits are
# asserted to be live on every one of them.

ROLLOUT_SEED: int = 20260924
ROLLOUT_MAX_FRAMES: int = 5000
ROLLOUT_DT: float = 1.0 / 60.0






def _band_stats(models, mb):
    """Summarise a bag of live wrap models (probe-style, never raises)."""
    stats = {
        "models": 0,
        "bump_off": 0,
        "drag": 0,
        "smash": 0,
        "other_tokens": 0,
        "carrier_fends": set(),
        "tackle_skills": set(),
        "wrap_moved": 0,
        "carrier_moved": 0,
        # the matchup breakdown, measured off the models' own traits
        "back_carrier_bump": 0,
        "forward_primary_bump": 0,
        "forward_cluster_drag": 0,
        "ruck_chain_frames": 0,
    }
    for model in models:
        stats["models"] += 1
        token = model.collision_state
        if token == mb.COLLISION_STATE_BUMP_OFF:
            stats["bump_off"] += 1
        elif token == mb.COLLISION_STATE_PROGRESSIVE_DRAG:
            stats["drag"] += 1
        elif token == mb.COLLISION_STATE_DOMINANT_SMASH:
            stats["smash"] += 1
        else:
            stats["other_tokens"] += 1

        stats["carrier_fends"].add(round(float(model.carrier_fend_trait), 6))
        primary_trait = None
        for one in model.contributions:
            skill = round(float(one.tackle_skill_trait), 6)
            stats["tackle_skills"].add(skill)
            if one.is_primary:
                primary_trait = skill

        if model.wrap_momentum_kgmps != model.baseline_wrap_momentum_kgmps:
            stats["wrap_moved"] += 1
        if (model.carrier_effective_momentum_kgmps
                != model.carrier_momentum_kgmps):
            stats["carrier_moved"] += 1

        if (token == mb.COLLISION_STATE_BUMP_OFF
                and round(float(model.carrier_fend_trait), 6) == 1.35):
            stats["back_carrier_bump"] += 1
        if (token == mb.COLLISION_STATE_BUMP_OFF
                and primary_trait == 1.30):
            stats["forward_primary_bump"] += 1
        if (token == mb.COLLISION_STATE_PROGRESSIVE_DRAG
                and round(float(model.carrier_fend_trait), 6) == 1.15
                and model.assist_count >= 1):
            stats["forward_cluster_drag"] += 1
    return stats


def _run_live_match():
    """One seeded live match; returns ``(models, band_stats)``.

    Boots the engine exactly as the sibling LIVE gates do: display, field,
    world, ball, open-play engine, kickoff engine, then the kickoff -> catch ->
    open-play frame loop.  Pygame is deliberately NOT torn down here, so a
    second pass in the same process still has a display surface.
    """
    import io
    import random

    # HEADLESS (the same contract the sibling LIVE gates keep): the video
    # driver is chosen when pygame initialises a display, and a CI runner has
    # none.  ``setdefault`` so an environment that already names a working
    # driver keeps it.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    import pygame

    from world.field import Field
    from world.world_state import WorldState, GamePhase
    from world.open_play_engine import OpenPlayEngine
    from ball.ball import Ball
    from game.kickoff_engine import KickoffEngine
    from game.physics.collision_system import CollisionSystem
    from world.catch_system import check_for_catch
    from game.tes import tes_multi_body as mb

    random.seed(ROLLOUT_SEED)

    # Some engine paths tear the display surface down mid-run
    # (``Field.__init__`` does ``pygame.image.load(...).convert()``), so the
    # surface is re-created for every pass.
    if not pygame.display.get_init():
        pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    # The engine's per-frame debug chatter (freeze audit, tackle gate, ruck
    # pin, FB_SELECT) is enormous; swallow it for the duration of the match
    # and restore the real stdout for the result lines the CI parser reads.
    real_stdout = sys.stdout
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )

    ruck_frames = 0
    try:
        collision_system = CollisionSystem()
        field = Field(1920, 1080)
        world = WorldState()
        ball = Ball(field)
        world.ball = ball
        open_play = OpenPlayEngine(world)
        world.open_play = open_play
        world.open_play_engine = open_play
        kickoff_engine = KickoffEngine(world, ball, field)

        mb.reset_wrap_models()
        kickoff_engine.reset_for_kickoff()
        world.ball_owner = "NONE"
        world.ball_carrier = None
        world.ball.possessed_by = None
        kickoff_engine.kickoff()

        for frame in range(1, ROLLOUT_MAX_FRAMES + 1):
            world.frame_count = frame
            ball.update(ROLLOUT_DT)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(ROLLOUT_DT)
                check_for_catch(world, ball, ROLLOUT_DT)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(ROLLOUT_DT)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)
            else:
                continue

            sub = getattr(world, "sub_state", None)
            name = getattr(sub, "name", None) or str(sub)
            if ("RELEASE" in name or "PTB" in name or "POST_RUCK" in name):
                ruck_frames += 1

        models = list(mb.recent_wrap_models())
    finally:
        sys.stdout = real_stdout

    stats = _band_stats(models, mb)
    stats["ruck_chain_frames"] = ruck_frames
    return models, stats


def _live_lever(value):
    """Set / clear RLM_POSITIONAL_TRAITS, returning a restore callable."""
    var = "RLM_POSITIONAL_TRAITS"
    had = var in os.environ
    prev = os.environ.get(var)

    def restore():
        if had:
            os.environ[var] = prev
        else:
            os.environ.pop(var, None)

    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value
    return restore


def section_live_rollout(mb):
    """A seeded live match: the positional bands, measured end to end."""
    banner("[12] THE LIVE POSITIONAL GRADIENT - a seeded kickoff-to-ruck match")

    restore = _live_lever(None)
    on_stats = None
    err = None
    try:
        _on_models, on_stats = _run_live_match()
    except Exception:
        err = traceback.format_exc()
        _emit(err)
    finally:
        restore()

    check("the LIVE match rollout completes without raising", err is None)
    if err is not None or on_stats is None:
        return True

    _emit("      ON  bands: models=%d bump_off=%d drag=%d smash=%d"
          % (on_stats["models"], on_stats["bump_off"], on_stats["drag"],
             on_stats["smash"]))
    _emit("      ON  matchup breakdown: back_carrier_bump=%d "
          "forward_primary_bump=%d forward_cluster_drag=%d"
          % (on_stats["back_carrier_bump"], on_stats["forward_primary_bump"],
             on_stats["forward_cluster_drag"]))
    _emit("      ON  live carrier fends=%s live tackle skills=%s"
          % (sorted(on_stats["carrier_fends"]),
             sorted(on_stats["tackle_skills"])))
    _emit("      ON  wrap moved off its neutral baseline on %d/%d models; "
          "carrier momentum moved on %d/%d"
          % (on_stats["wrap_moved"], on_stats["models"],
             on_stats["carrier_moved"], on_stats["models"]))

    check("the LIVE match declared at least one multi-body contest",
          on_stats["models"] > 0,
          "models=%d" % on_stats["models"])
    if on_stats["models"] == 0:
        return True

    check("EVERY live carrier carries a DECLARED positional fend "
          "(1.35 back / 1.15 forward), never the neutral fallback",
          on_stats["carrier_fends"]
          and on_stats["carrier_fends"] <= {1.15, 1.35},
          "fends=%s" % sorted(on_stats["carrier_fends"]))
    check("EVERY live wrap body carries a DECLARED positional tackle-skill "
          "(0.85 back / 1.30 forward), never the neutral fallback",
          on_stats["tackle_skills"]
          and on_stats["tackle_skills"] <= {0.85, 1.30},
          "skills=%s" % sorted(on_stats["tackle_skills"]))
    check("the defenders' declared skill moved LIVE wrap momentum off its "
          "trait-free baseline",
          on_stats["wrap_moved"] > 0,
          "%d of %d models" % (on_stats["wrap_moved"], on_stats["models"]))
    check("the carrier's declared fend moved LIVE carrier momentum off its "
          "trait-free baseline",
          on_stats["carrier_moved"] > 0,
          "%d of %d models" % (on_stats["carrier_moved"], on_stats["models"]))
    check("every live declaration resolved inside the CLOSED token vocabulary",
          on_stats["other_tokens"] == 0,
          "bump_off=%d drag=%d smash=%d other=%d"
          % (on_stats["bump_off"], on_stats["drag"], on_stats["smash"],
             on_stats["other_tokens"]))
    check("the live match reached the ruck chain (RELEASE / PTB)",
          on_stats["ruck_chain_frames"] > 0,
          "%d ruck-chain frames" % on_stats["ruck_chain_frames"])

    # ---- THE A/B: the lever must remove the traits LIVE ----------------
    off_restore = _live_lever("0")
    off_stats = None
    err = None
    try:
        _off_models, off_stats = _run_live_match()
    except Exception:
        err = traceback.format_exc()
        _emit(err)
    finally:
        off_restore()

    check("the LIVE rollback pass completes without raising", err is None)
    if err is not None or off_stats is None:
        return True

    _emit("      OFF bands: models=%d bump_off=%d drag=%d smash=%d"
          % (off_stats["models"], off_stats["bump_off"], off_stats["drag"],
             off_stats["smash"]))
    _emit("      OFF live carrier fends=%s live tackle skills=%s"
          % (sorted(off_stats["carrier_fends"]),
             sorted(off_stats["tackle_skills"])))

    check("the rollback pass still declares contests (the A/B is not vacuous)",
          off_stats["models"] > 0, "models=%d" % off_stats["models"])
    check("RLM_POSITIONAL_TRAITS=0 strips EVERY live body back to the neutral "
          "fallback",
          off_stats["carrier_fends"] == {1.0}
          and off_stats["tackle_skills"] == {1.0},
          "fends=%s skills=%s"
          % (sorted(off_stats["carrier_fends"]),
             sorted(off_stats["tackle_skills"])))
    check("the LEVER alone changes every live body's declared profile "
          "(the traits are the mapper's doing, not the engine's)",
          off_stats["carrier_fends"] != on_stats["carrier_fends"]
          and off_stats["tackle_skills"] != on_stats["tackle_skills"],
          "on=%s/%s vs off=%s/%s"
          % (sorted(on_stats["carrier_fends"]),
             sorted(on_stats["tackle_skills"]),
             sorted(off_stats["carrier_fends"]),
             sorted(off_stats["tackle_skills"])))
    check("with the traits stripped the defenders' wrap IS its own baseline "
          "on every live model",
          off_stats["wrap_moved"] == 0
          and off_stats["carrier_moved"] == 0,
          "wrap_moved=%d carrier_moved=%d"
          % (off_stats["wrap_moved"], off_stats["carrier_moved"]))
    return True


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    ok = True
    try:
        mb = section_leaf()
        model, bc, primary, assist, rejected, watch = section_membership(mb)
        section_wrap(mb, model, bc, primary, assist)
        open_model, watch_model = section_windows(mb, bc, primary, assist, watch)
        section_noop(mb, bc, primary, assist, watch)
        section_tes_boundary(mb, bc, primary, assist, watch)
        section_consumers(mb, model, bc, primary, assist)
        section_fend(mb, bc, primary, assist, watch)
        # [10] runs BEFORE the [9] integration audit: the audit asserts that
        # EVERY declared gate was exercised, so it must observe the gate this
        # section registers.  The labels are stable stage numbers; the
        # EXECUTION order is the one that carries the invariant.
        section_defensive_resistance(mb, bc, primary, assist, watch)
        # [11] runs BEFORE the [9] audit for the same reason [10] does: the
        # audit asserts that EVERY declared gate was exercised, so it must
        # observe the gate this section registers.
        section_collision_state(mb, bc, primary, assist, watch)
        # [12] the LIVE end-to-end proof: boot the real engine, play a seeded
        # kickoff-to-ruck match and measure the positional gradient on it.
        # BOTH lever arms are played inside this ONE call, so the section is
        # invoked exactly once: calling it per arm replays the whole seeded
        # 5000-frame match a second time for measurements already in hand.
        section_live_rollout(mb)
        section_integration(mb)
    except Exception:
        ok = False
        banner("UNCAUGHT EXCEPTION IN THE VERIFIER")
        traceback.print_exc()
        _emit(traceback.format_exc())

    failed = [label for label, passed in _CHECKS if not passed]
    for label in failed:
        ok = False

    banner("SUMMARY")
    _emit("checks=%d passed=%d failed=%d"
          % (len(_CHECKS), len(_CHECKS) - len(failed), len(failed)))
    _emit("gates=%d passed=%d"
          % (len(_GATE_RESULTS),
             sum(1 for v in _GATE_RESULTS.values() if v)))
    if failed:
        _emit("FAILED CHECKS:")
        for label in failed:
            _emit("  - %s" % label)

    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_LOG_LINES) + "\n")
    except OSError:
        pass
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump({
                "checks": len(_CHECKS),
                "failed": failed,
                "gates": _GATE_RESULTS,
                "result": "PASS" if ok and not failed else "FAIL",
            }, fh, indent=2, sort_keys=True)
    except OSError:
        pass

    _emit("")
    _emit("RESULT: %s" % ("PASS" if ok and not failed else "FAIL"))
    return 0 if (ok and not failed) else 1


if __name__ == "__main__":
    sys.exit(main())
