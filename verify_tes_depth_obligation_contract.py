"""verify_tes_depth_obligation_contract.py — STAGE-1 CONTRACT VERIFIER.

Machine-checks every LOCKED Stage-1 decision about
``game.defence.defence_lvl_1.tes_depth_obligation``:

  [1] Debt semantics — SIGNED error, UNSIGNED debt, strict tolerance
  [2] Exemption — tacklers / markers / DH / fullback exempt BY DEFINITION
  [3] Era gate — measure-wide (drive era), obligate-in-chamber (TES only)
  [4] Role depth — single source (live stamp first, canonical law second)
  [5] No invented state — the module writes nothing and latches nothing
  [6] Wiring — measurement-only: ONE sanctioned consumer (its Stage-2
      successor), one tuner reader, no behavioural importer

The verifier is PURE and HEADLESS: it drives the definition module with
attribute STUBS, so it never boots the engine and never depends on the
run-to-run engine non-determinism that makes a full roll-out A/B
inconclusive (identical engine code has been recorded producing different
worlds; see ``probe_tes_side_clamp_proof.py``).

Exit code: 0 = all checks passed, 1 = at least one check failed.

Run:  python verify_tes_depth_obligation_contract.py
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import ast
import json
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from player.player import DefenderRole
from game.defence.defence_lvl_1 import tes_depth_obligation as tdo
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

MODULE_NAME = "tes_depth_obligation"
MODULE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "tes_depth_obligation.py",
)
TUNER_NAME = "tes_depth_obligation_tolerance_m"
SCAN_DIRS = ("game", "world", "player", "ball", "kick_off", "ai_agents")
REPORT_PATH = "verify_tes_depth_obligation_report.json"

# Phase names used by the stubs.  Two spellings are exercised on purpose:
# the lower-cased sub-state value (the live engine shape) and one upper
# case string, to prove the chamber test normalises.
CHAMBER_PHASES = (
    "tackle_init", "tackle_contact", "tackle_drive", "tackle_dominance",
)

RESULTS = []


def check(section, name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({
        "section": section, "name": name, "ok": ok, "detail": detail,
    })
    print("  [%s] %s%s" % (
        "PASS" if ok else "FAIL", name,
        (" — %s" % detail) if detail else "",
    ))
    return ok


def section(title):
    print("\n%s" % title)


# ─────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────

def stub_world(phase="tackle_contact", with_carrier=True, **attrs):
    """A world stub whose sub-state drives the chamber test."""
    world = SimpleNamespace(sub_state=phase)
    world.ball_carrier = object() if with_carrier else None
    for key, value in attrs.items():
        setattr(world, key, value)
    return world


def stub_defender(
    pid=1, role="DEF_LINE_READY", depth_role="D",
    x=30.0, target=25.0, **attrs
):
    """A defender stub carrying exactly the attributes the module reads."""
    d = SimpleNamespace(
        player_id=pid,
        depth_role=depth_role,
        x=x,
        role_depth_target_x=target,
        vx=0.0,
    )
    d.defender_role = None if role is None else getattr(DefenderRole, role)
    for key, value in attrs.items():
        setattr(d, key, value)
    return d


TUNERS = DefenceTuners()
TOL = tdo.tes_depth_tolerance_m(TUNERS)


# ─────────────────────────────────────────────────────────────────────
# [1] DEBT SEMANTICS
# ─────────────────────────────────────────────────────────────────────

def verify_debt_semantics():
    section("[1] Debt semantics (signed error / unsigned debt / tolerance)")
    world = stub_world()

    ahead = stub_defender(x=27.0, target=25.0)
    behind = stub_defender(x=23.0, target=25.0)
    aligned = stub_defender(x=25.0, target=25.0)

    err_ahead = tdo.tes_depth_error_m(world, ahead, target_x=25.0)
    err_behind = tdo.tes_depth_error_m(world, behind, target_x=25.0)
    check(1, "error is SIGNED: ahead of role depth is positive",
          err_ahead > 0.0, "error=%+.2f" % err_ahead)
    check(1, "error is SIGNED: behind role depth is negative",
          err_behind < 0.0, "error=%+.2f" % err_behind)
    check(1, "error is exactly x - role_depth",
          abs(err_ahead - 2.0) < 1e-9, "error=%+.3f" % err_ahead)

    check(1, "debt is UNSIGNED: debt == abs(error) for both sides",
          (tdo.tes_depth_debt_m(world, ahead, target_x=25.0) == 2.0
           and tdo.tes_depth_debt_m(world, behind, target_x=25.0) == 2.0),
          "ahead=%.2f behind=%.2f" % (
              tdo.tes_depth_debt_m(world, ahead, target_x=25.0),
              tdo.tes_depth_debt_m(world, behind, target_x=25.0),
          ))
    check(1, "debt is 0.0 exactly ON the role depth",
          tdo.tes_depth_debt_m(world, aligned, target_x=25.0) == 0.0)
    check(1, "on-role-depth body is NOT obligated",
          not tdo.is_tes_depth_obligated(world, aligned, TUNERS))

    check(1, "verdict is DIRECTION-AGNOSTIC: equal |error|, same verdict",
          (tdo.is_tes_depth_obligated(world, ahead, TUNERS)
           == tdo.is_tes_depth_obligated(world, behind, TUNERS)
           is True),
          "ahead=%s behind=%s" % (
              tdo.is_tes_depth_obligated(world, ahead, TUNERS),
              tdo.is_tes_depth_obligated(world, behind, TUNERS),
          ))

    # STRICT gate: a body exactly ON the tolerance is INSIDE the band.
    on_tol = stub_defender(x=25.0 + TOL, target=25.0)
    over_tol = stub_defender(x=25.0 + TOL + 0.01, target=25.0)
    check(1, "tolerance gate is STRICT (debt == tolerance is inside)",
          not tdo.is_tes_depth_obligated(world, on_tol, TUNERS),
          "debt=%.2f tol=%.2f" % (TOL, TOL))
    check(1, "one centimetre past tolerance IS obligated",
          tdo.is_tes_depth_obligated(world, over_tol, TUNERS))

    check(1, "tolerance comes from the tuner (0.5 m, the live hold deadband)",
          abs(TOL - 0.5) < 1e-9
          and abs(TOL - TUNERS.tes_depth_obligation_tolerance_m) < 1e-9,
          "tol=%.2f" % TOL)
    check(1, "malformed tolerance degrades to the module fallback",
          tdo.tes_depth_tolerance_m(
              SimpleNamespace(tes_depth_obligation_tolerance_m="junk")
          ) == tdo.TES_DEPTH_TOLERANCE_FALLBACK_M)
    check(1, "negative tolerance degrades to the module fallback",
          tdo.tes_depth_tolerance_m(
              SimpleNamespace(tes_depth_obligation_tolerance_m=-1.0)
          ) == tdo.TES_DEPTH_TOLERANCE_FALLBACK_M)

    big = stub_defender(x=40.0, target=25.0)
    check(1, "no measurable target => error 0.0 and debt 0.0 (safe to sum)",
          (tdo.tes_depth_error_m(world, big, target_x=float("nan")) == 0.0
           and tdo.tes_depth_debt_m(world, big, target_x=float("nan"))
           == 0.0))
    check(1, "None defender is safe: error 0.0, debt 0.0, not obligated",
          (tdo.tes_depth_error_m(world, None) == 0.0
           and tdo.tes_depth_debt_m(world, None) == 0.0
           and not tdo.is_tes_depth_obligated(world, None, TUNERS)))


# ─────────────────────────────────────────────────────────────────────
# [2] EXEMPTION
# ─────────────────────────────────────────────────────────────────────

def verify_exemption():
    section("[2] Exemption — contact authority and depth authority never "
            "compete")
    world = stub_world()

    for role in ("MARKER_A", "MARKER_B", "DUMMY_HALF",
                 "TACKLER_1", "TACKLER_2", "TACKLER_3"):
        d = stub_defender(role=role, x=40.0, target=25.0)
        check(2, "%s is exempt" % role,
              tdo.is_depth_exempt_role(d, world))
        check(2, "%s is NOT obligated despite 15 m debt" % role,
              not tdo.is_tes_depth_obligated(world, d, TUNERS))

    fb = stub_defender(x=40.0, target=25.0, fullback_role_active=True)
    check(2, "the dynamic fullback is exempt",
          tdo.is_depth_exempt_role(fb, world))
    check(2, "the dynamic fullback is NOT obligated",
          not tdo.is_tes_depth_obligated(world, fb, TUNERS))

    flagged = stub_defender(x=40.0, target=25.0, is_tackler=True)
    check(2, "is_tackler flag exempts (armed before TACKLER_n is stamped)",
          tdo.is_depth_exempt_role(flagged, world)
          and not tdo.is_tes_depth_obligated(world, flagged, TUNERS))

    reg = stub_defender(pid=7, x=40.0, target=25.0)
    w_ids = stub_world(tackler_ids=[7])
    check(2, "world.tackler_ids exempts",
          tdo.is_depth_exempt_role(reg, w_ids)
          and not tdo.is_tes_depth_obligated(w_ids, reg, TUNERS))

    prim = stub_defender(pid=9, x=40.0, target=25.0)
    w_prim = stub_world(primary_tackler_id=9)
    check(2, "world.primary_tackler_id exempts",
          tdo.is_depth_exempt_role(prim, w_prim)
          and not tdo.is_tes_depth_obligated(w_prim, prim, TUNERS))

    sec = stub_defender(pid=11, x=40.0, target=25.0)
    w_sec = stub_world(secondary_tacklers=[11])
    check(2, "world.secondary_tacklers exempts",
          tdo.is_depth_exempt_role(sec, w_sec)
          and not tdo.is_tes_depth_obligated(w_sec, sec, TUNERS))

    line = stub_defender(role="DEF_LINE_READY", x=40.0, target=25.0)
    check(2, "DEF_LINE_READY with a role depth is NOT exempt",
          not tdo.is_depth_exempt_role(line, world))
    check(2, "DEF_LINE_READY with 15 m debt IS obligated",
          tdo.is_tes_depth_obligated(world, line, TUNERS))

    # The exemption is keyed on the DEFENDER role, not on the role depth:
    # a body that owns a role depth but carries no defender_role is still
    # in the obligation population (a real open-play shape — a body whose
    # defender_role has not been stamped yet).
    unstamped = stub_defender(
        pid=207, role=None, depth_role="C", x=40.0, target=25.0,
    )
    check(2, "a body with a role depth but NO defender_role is NOT exempt",
          not tdo.is_depth_exempt_role(unstamped, world))
    check(2, "...and it IS obligated (depth ownership, not role naming)",
          tdo.is_tes_depth_obligated(world, unstamped, TUNERS))

    check(2, "None defender is exempt (safe default)",
          tdo.is_depth_exempt_role(None, world) is True)


# ─────────────────────────────────────────────────────────────────────
# [3] ERA GATE
# ─────────────────────────────────────────────────────────────────────

def verify_era_gate():
    section("[3] Era gate — debt is ERA-wide, the obligation is CHAMBER-only")
    check(3, "the chamber set is exactly the four TES phases",
          set(tdo.TES_DEPTH_OBLIGATION_PHASES) == set(CHAMBER_PHASES),
          "phases=%s" % sorted(tdo.TES_DEPTH_OBLIGATION_PHASES))

    for phase in CHAMBER_PHASES:
        world = stub_world(phase=phase)
        check(3, "chamber is TRUE in %s" % phase,
              tdo.in_tes_depth_chamber(world))

    upper = stub_world(phase="TACKLE_CONTACT")
    check(3, "the chamber test normalises case",
          tdo.in_tes_depth_chamber(upper))

    check(3, "chamber is FALSE in plain open_play",
          not tdo.in_tes_depth_chamber(stub_world(phase="open_play")))
    check(3, "chamber is FALSE with no live carrier",
          not tdo.in_tes_depth_chamber(
              stub_world(phase="tackle_contact", with_carrier=False)
          ))
    check(3, "chamber is FALSE for a None world",
          not tdo.in_tes_depth_chamber(None))

    # THE LOCKED SPLIT, stated behaviourally: the DEBT is measurable in
    # open_play, the OBLIGATION is not.
    op = stub_world(phase="open_play")
    d = stub_defender(x=29.0, target=25.0)
    check(3, "the debt MEASURE is valid in plain open_play",
          tdo.tes_depth_debt_m(op, d) == 4.0,
          "debt=%.2f" % tdo.tes_depth_debt_m(op, d))
    check(3, "but NO body is obligated in plain open_play",
          not tdo.is_tes_depth_obligated(op, d, TUNERS))

    # The era predicate DELEGATES to the engine's single drive-phase law.
    for phase in CHAMBER_PHASES + ("open_play", "ptb_ready"):
        world = stub_world(phase=phase)
        try:
            from game.defence.defence_lvl_1.defensive_width import (
                is_drive_phase,
            )
            expected = bool(is_drive_phase(world))
            via = "is_drive_phase"
        except Exception:
            expected = bool(
                world.ball_carrier is not None
                and phase in (tdo.TES_DEPTH_OBLIGATION_PHASES
                              | frozenset({"open_play"}))
            )
            via = "fallback"
        got = tdo.in_tes_depth_era(world)
        check(3, "era(%s) delegates to the drive-phase law [%s]"
              % (phase, via),
              got == expected, "got=%s expected=%s" % (got, expected))

    check(3, "era is FALSE for a None world",
          not tdo.in_tes_depth_era(None))


# ─────────────────────────────────────────────────────────────────────
# [4] ROLE DEPTH — SINGLE SOURCE
# ─────────────────────────────────────────────────────────────────────

def verify_role_depth_source():
    section("[4] Role depth — the engine's own published stamp is preferred")
    world = stub_world()

    stamped = stub_defender(x=30.0, target=11.0)
    check(4, "the live role_depth_target_x stamp is used verbatim",
          tdo.tes_depth_target_x(world, stamped) == 11.0,
          "target=%.2f" % tdo.tes_depth_target_x(world, stamped))

    nan_stamped = stub_defender(x=30.0, target=float("nan"))
    got = tdo.tes_depth_target_x(world, nan_stamped)
    check(4, "a non-finite stamp is IGNORED (never returned)",
          got is None or (math.isfinite(got) and got != got),
          "target=%s" % got)

    inf_stamped = stub_defender(x=30.0, target=float("inf"))
    got_inf = tdo.tes_depth_target_x(world, inf_stamped)
    check(4, "an infinite stamp is IGNORED",
          got_inf is None or math.isfinite(got_inf),
          "target=%s" % got_inf)

    bool_stamped = stub_defender(x=30.0, target=True)
    got_bool = tdo.tes_depth_target_x(world, bool_stamped)
    check(4, "a bool stamp is IGNORED (bools are not depths)",
          got_bool is not True, "target=%s" % got_bool)

    # An UNSTAMPED body falls back to the canonical law, which may or may
    # not resolve on a bare stub world.  The contract asserted here is the
    # SAFETY property the callers depend on: the fallback never yields a
    # non-finite target (an infinite target would make every debt infinite).
    unstamped = stub_defender(x=30.0)
    del unstamped.role_depth_target_x
    got_unstamped = tdo.tes_depth_target_x(stub_world(with_carrier=False),
                                          unstamped)
    check(4, "an unstamped body yields None or a FINITE canonical target",
          got_unstamped is None
          or math.isfinite(float(got_unstamped)),
          "target=%s" % got_unstamped)

    check(4, "a None defender has no target",
          tdo.tes_depth_target_x(world, None) is None)


# ─────────────────────────────────────────────────────────────────────
# [5] NO INVENTED STATE
# ─────────────────────────────────────────────────────────────────────

FORBIDDEN_PREFIXES = (
    "stamp_", "clear_", "write_", "set_", "arm_", "latch_",
)
FORBIDDEN_NAMES = (
    "OBLIGATION_ATTR",
    "OBLIGATION_TARGET_ATTR",
    "OBLIGATION_DISCHARGED_FRAME_ATTR",
    "REARM_COOLDOWN_FRAMES",
    "REARM_MARGIN_M",
    "RETREAT_OBLIGATION_TOLERANCE_M",
)
FORBIDDEN_SOURCE_TOKENS = (
    "POST_RUCK_DURATION_S",
    "phase_timer",
    "set_phase",
    "release_controller",
)


def verify_no_invented_state():
    section("[5] No invented state — the module writes nothing")
    public = [n for n in dir(tdo) if not n.startswith("__")]

    bad_prefix = [n for n in public
                  if any(n.startswith(p) for p in FORBIDDEN_PREFIXES)]
    check(5, "no stamp_/clear_/write_/set_/arm_/latch_ API is defined",
          not bad_prefix, "offenders=%s" % bad_prefix)

    bad_names = [n for n in FORBIDDEN_NAMES if n in public]
    check(5, "no latch/target/re-arm attribute name is defined",
          not bad_names, "offenders=%s" % bad_names)

    with open(MODULE_PATH, "r", encoding="utf-8") as fh:
        src = fh.read()
    bad_tokens = [t for t in FORBIDDEN_SOURCE_TOKENS if t in src]
    check(5, "the module touches no phase timer / release constant",
          not bad_tokens, "offenders=%s" % bad_tokens)

    world = stub_world()
    d = stub_defender(x=40.0, target=25.0)
    before_d = dict(vars(d))
    before_w = dict(vars(world))

    tdo.is_tes_depth_obligated(world, d, TUNERS)
    tdo.tes_depth_debt_m(world, d)
    tdo.tes_depth_error_m(world, d)
    tdo.format_depth_obligation_report(world, [d], TUNERS)

    check(5, "the verdict writes NOTHING onto the defender",
          dict(vars(d)) == before_d,
          "added=%s" % sorted(set(vars(d)) - set(before_d)))
    check(5, "the verdict writes NOTHING onto the world",
          dict(vars(world)) == before_w,
          "added=%s" % sorted(set(vars(world)) - set(before_w)))

    first = tdo.is_tes_depth_obligated(world, d, TUNERS)
    second = tdo.is_tes_depth_obligated(world, d, TUNERS)
    check(5, "the verdict is a PURE function (no hidden state)",
          first == second is True)

    report = tdo.format_depth_obligation_report(world, [d], TUNERS)
    check(5, "the diagnostic report names the obligated body",
          "id=%s" % d.player_id in report, report[:70])
    check(5, "the report is clear when no body owes depth",
          "clear" in tdo.format_depth_obligation_report(
              stub_world(), [stub_defender(x=25.0, target=25.0)], TUNERS,
          ))


# ─────────────────────────────────────────────────────────────────────
# [6] WIRING — MEASUREMENT ONLY
# ─────────────────────────────────────────────────────────────────────

def _imports_module(source):
    """True when *source* really IMPORTS the obligation module (AST)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == MODULE_NAME:
                return True
            for alias in node.names:
                if alias.name == MODULE_NAME:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == MODULE_NAME:
                    return True
    return False


def _store_node_ids(tree):
    """Ids of the nodes that are ASSIGNMENT TARGETS in *tree*.

    ``self.tes_depth_obligation_tolerance_m = ...`` is an ``ast.Attribute``
    exactly like the read inside the definition module, so a naive scan
    reports the DEFINITION SITE as a reader.  Stores are excluded.
    """
    stores = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                stores.add(id(sub))
    return stores


def _count_tuner_refs(source):
    """Number of NON-STORE references to the Stage-1 tuner name."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    stores = _store_node_ids(tree)
    count = 0
    for node in ast.walk(tree):
        if id(node) in stores:
            continue
        if isinstance(node, ast.Attribute) and node.attr == TUNER_NAME:
            count += 1
        elif isinstance(node, ast.Name) and node.id == TUNER_NAME:
            count += 1
        elif (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value == TUNER_NAME):
            count += 1
    return count


def _references_tuner(source):
    """True when *source* READS the Stage-1 tuner (stores do not count)."""
    return _count_tuner_refs(source) > 0


def _iter_engine_sources():
    for root in SCAN_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.normpath(path) == os.path.normpath(MODULE_PATH):
                    continue
                try:
                    with open(path, "r", encoding="utf-8",
                              errors="replace") as fh:
                        yield path, fh.read()
                except OSError:
                    continue


def verify_wiring():
    section("[6] Wiring — Stage 1 must stay measurement-only")

    # ── SUPERSEDED CLAUSE, NARROWED AT THE STAGE-1 -> STAGE-2 BOUNDARY ──
    # Stage 1's original clause was "NO engine module imports
    # tes_depth_obligation", which encoded Stage 1's MEASUREMENT-ONLY
    # status.  Stage 2 deliberately supersedes exactly that: the successor
    # authority (``tes_depth_authority``) CONSUMES the Stage-1 truth rather
    # than re-declaring it, and re-declaring the debt/target is what the
    # Stage-2 contract forbids.  The invariant that survives the transition
    # is therefore narrower and stronger: Stage 1 is imported by its
    # SANCTIONED CONSUMER AND NOTHING ELSE — no behavioural module, no
    # phase handler, no manager.  Stage 1 still carries no authority of its
    # own (asserted below), so it remains measurement-only in itself.
    SANCTIONED_CONSUMERS = {"tes_depth_authority.py"}

    importers = [p for p, s in _iter_engine_sources()
                 if _imports_module(s)]
    unsanctioned = [p for p in importers
                    if os.path.basename(p) not in SANCTIONED_CONSUMERS]
    check(6, "Stage 1 is imported ONLY by its sanctioned Stage-2 successor",
          not unsanctioned,
          "importers=%s unsanctioned=%s" % (importers, unsanctioned))
    # The successor must actually be there — an empty importer set would
    # mean Stage 1 has been orphaned rather than superseded.
    check(6, "the Stage-2 successor is present and consumes Stage 1",
          any(os.path.basename(p) in SANCTIONED_CONSUMERS
              for p in importers),
          "importers=%s" % importers)

    readers = [p for p, s in _iter_engine_sources()
               if _references_tuner(s)]
    check(6, "the Stage-1 tuner is read ONLY inside the definition module",
          not readers, "offenders=%s" % readers)

    with open(MODULE_PATH, "r", encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    module_scope_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_scope_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_scope_imports.append(node.module or "")
    non_stdlib = [m for m in module_scope_imports
                  if m.split(".")[0] not in ("__future__", "math", "typing")]
    check(6, "module-scope imports are stdlib only (no import cycle)",
          not non_stdlib, "offenders=%s" % non_stdlib)
    # The module READS the tuner exactly once (``getattr(tuners, ...)`` in
    # ``tes_depth_tolerance_m``).  The docstring also names it in prose,
    # which is why this counts AST nodes rather than source substrings.
    module_refs = _count_tuner_refs(src)
    check(6, "the definition module READS the tuner exactly once",
          module_refs == 1, "reads=%d" % module_refs)


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("TES DEPTH OBLIGATION CONTRACT VERIFICATION")
    print("(Stage 1: definition + measurement only — no authority)")
    print("=" * 72)

    verify_debt_semantics()
    verify_exemption()
    verify_era_gate()
    verify_role_depth_source()
    verify_no_invented_state()
    verify_wiring()

    failed = [r for r in RESULTS if not r["ok"]]
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "total": len(RESULTS),
            "passed": len(RESULTS) - len(failed),
            "failed": len(failed),
            "failures": failed,
            "checks": RESULTS,
        }, fh, indent=2)
    print("\nReport written to %s" % REPORT_PATH)

    print("\n" + "=" * 72)
    if failed:
        for r in failed:
            print("  FAILED: [%s] %s%s" % (
                r["section"], r["name"],
                (" — %s" % r["detail"]) if r["detail"] else "",
            ))
        print("RESULT: %d / %d CHECKS FAILED" % (len(failed), len(RESULTS)))
        return 1
    print("RESULT: ALL CHECKS PASSED (%d)" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
