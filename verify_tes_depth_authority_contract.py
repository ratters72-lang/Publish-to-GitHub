"""verify_tes_depth_authority_contract.py — STAGE-2 CONTRACT VERIFIER.

Machine-checks every LOCKED Stage-2 decision about
``game.defence.defence_lvl_1.tes_depth_authority`` — the depth-discharge
successor to the Stage-1 TES Depth Obligation:

  [1] The verdict — a PURE read of Stage 1, gated by the freeze contract
  [2] The convergence law — gap-proportional, floored, capped, tracking
  [3] The write set — exactly four per-frame fields, nothing else
  [4] The gate — tuner + runtime lever, safe on a bare namespace
  [5] No invented state — no latch / hysteresis / cooldown / target store
  [6] Single source + wiring — the chamber IS Stage 1's, wired in the TES
      drive path after the structure hold and before integration

The verifier is PURE and HEADLESS: it drives the authority with attribute
STUBS, so it never boots the engine and never depends on the run-to-run
engine non-determinism that makes a full roll-out A/B inconclusive.

Exit code: 0 = all checks passed, 1 = at least one check failed.

Run:  python verify_tes_depth_authority_contract.py
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
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1 import tes_depth_obligation as tdo
from game.defence.defence_lvl_1 import tes_depth_authority as tda
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

MODULE_NAME = "tes_depth_authority"
MODULE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "tes_depth_authority.py",
)
MANAGER_SRC = os.path.join("game", "managers", "defence_manager.py")
GUARD_SRC = os.path.join("game", "defence", "defence_lvl_1",
                         "phase_authority_guard.py")
OBLIGATION_SRC = os.path.join("game", "defence", "defence_lvl_1",
                             "tes_depth_obligation.py")
SCAN_DIRS = ("game", "world", "player", "ball", "kick_off", "ai_agents")
REPORT_PATH = "verify_tes_depth_authority_report.json"

CHAMBER_PHASES = (
    "tackle_init", "tackle_contact", "tackle_drive", "tackle_dominance",
)

# The per-frame fields the authority is allowed to write.  Anything outside
# this set on an authority-granted body is a contract violation.
ALLOWED_WRITES = ("target_x", "move_speed", "movement_state", "target_source")
# Fields the authority must NEVER write (owned by other authorities).
FORBIDDEN_WRITES = (
    "target_y", "vx", "vy", "frozen", "depth_locked",
    "depth_lock_x", "depth_lock_y",
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


def read_src(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ─────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────

def stub_world(phase="tackle_contact", carrier_vx=0.0, **attrs):
    """A world stub whose sub-state drives the chamber test."""
    world = SimpleNamespace(sub_state=phase)
    world.ball_carrier = SimpleNamespace(vx=float(carrier_vx))
    for key, value in attrs.items():
        setattr(world, key, value)
    return world


def stub_defender(pid=1, role="DEF_LINE_READY", depth_role="D",
                  x=30.0, target=25.0, frozen=False, **attrs):
    """A defender stub carrying exactly the attributes the modules read.

    ``target=None`` is the "no measurable role depth" case, so the stub
    must carry ``role_depth_target_x = None`` there rather than raising.
    """
    d = SimpleNamespace(
        player_id=pid,
        depth_role=depth_role,
        x=float(x),
        y=40.0,
        role_depth_target_x=(None if target is None else float(target)),
        vx=0.0,
        vy=0.0,
        frozen=bool(frozen),
        movement_state=MovementState.DRIFT,
        target_x=float(x),
        target_y=40.0,
        move_speed=0.0,
        target_source="structured_open_play",
    )
    d.defender_role = None if role is None else getattr(DefenderRole, role)
    for key, value in attrs.items():
        setattr(d, key, value)
    return d


TUNERS = DefenceTuners()
TOL = tdo.tes_depth_tolerance_m(TUNERS)


# ─────────────────────────────────────────────────────────────────────
# [1] THE VERDICT
# ─────────────────────────────────────────────────────────────────────

def verify_verdict():
    section("[1] The verdict — pure read of Stage 1, gated by the freeze "
            "contract")

    world = stub_world()
    obligated = stub_defender(x=40.0, target=25.0)
    check(1, "an obligated, unfrozen body IS under the authority",
          tda.is_tes_depth_authority_active(world, obligated, TUNERS))
    check(1, "the verdict agrees with the Stage-1 obligation",
          tda.is_tes_depth_authority_active(world, obligated, TUNERS)
          == tdo.is_tes_depth_obligated(world, obligated, TUNERS))

    frozen = stub_defender(x=40.0, target=25.0, frozen=True)
    check(1, "a FROZEN obligated body is NOT granted locomotion authority "
             "(TACKLE_INIT pins the line roles)",
          tdo.is_tes_depth_obligated(world, frozen, TUNERS)
          and not tda.is_tes_depth_authority_active(world, frozen, TUNERS))

    aligned = stub_defender(x=25.0, target=25.0)
    check(1, "a body inside tolerance is NOT under the authority",
          not tda.is_tes_depth_authority_active(world, aligned, TUNERS))

    on_tol = stub_defender(x=25.0 + TOL, target=25.0)
    over_tol = stub_defender(x=25.0 + TOL + 0.01, target=25.0)
    check(1, "the tolerance gate is inherited strictly from Stage 1",
          not tda.is_tes_depth_authority_active(world, on_tol, TUNERS)
          and tda.is_tes_depth_authority_active(world, over_tol, TUNERS))

    check(1, "a None defender is safe",
          tda.is_tes_depth_authority_active(world, None, TUNERS) is False)

    op = stub_world(phase="open_play")
    check(1, "no authority outside the TES chamber (plain open_play)",
          not tda.is_tes_depth_authority_active(
              op, stub_defender(x=40.0, target=25.0), TUNERS))

    for role in ("MARKER_A", "MARKER_B", "DUMMY_HALF",
                 "TACKLER_1", "TACKLER_2", "TACKLER_3"):
        ex = stub_defender(role=role, x=40.0, target=25.0)
        check(1, "%s is exempt (Stage-1 exemption read verbatim)" % role,
              not tda.is_tes_depth_authority_active(world, ex, TUNERS))

    fb = stub_defender(x=40.0, target=25.0, fullback_role_active=True)
    check(1, "the dynamic fullback is exempt",
          not tda.is_tes_depth_authority_active(world, fb, TUNERS))

    # THE VERDICT WRITES NOTHING (it is a read, not a grant).
    fresh = stub_defender(x=40.0, target=25.0, pid=99)
    before = dict(vars(fresh))
    tda.is_tes_depth_authority_active(world, fresh, TUNERS)
    check(1, "the verdict writes NO state onto the body",
          dict(vars(fresh)) == before,
          "added=%s" % sorted(set(vars(fresh)) - set(before)))


# ─────────────────────────────────────────────────────────────────────
# [2] THE CONVERGENCE LAW
# ─────────────────────────────────────────────────────────────────────

def verify_convergence_law():
    section("[2] The convergence law — gap-proportional, floored, capped, "
            "tracking")

    world = stub_world(carrier_vx=0.0)          # static carrier: pure gap law
    d = stub_defender()

    s0 = tda.tes_depth_convergence_mps(world, d, 0.0, TUNERS)
    s1 = tda.tes_depth_convergence_mps(world, d, 1.0, TUNERS)
    s4 = tda.tes_depth_convergence_mps(world, d, 4.0, TUNERS)
    s99 = tda.tes_depth_convergence_mps(world, d, 99.0, TUNERS)

    check(2, "speed is MONOTONE in the gap (more debt => more speed)",
          s1 < s4 < s99, "1m=%.2f 4m=%.2f 99m=%.2f" % (s1, s4, s99))
    check(2, "zero gap commands the floor (never zero within the law)",
          abs(s0 - TUNERS.tes_depth_authority_min_speed_mps) < 1e-9,
          "speed=%.2f" % s0)
    # The floor binds at small debts, so a proportional assertion must use
    # two points BOTH above the floor and BOTH below the cap.
    s3 = tda.tes_depth_convergence_mps(world, d, 3.0, TUNERS)
    s5 = tda.tes_depth_convergence_mps(world, d, 5.0, TUNERS)
    gain = TUNERS.tes_depth_authority_gain_per_s
    check(2, "the gap term is proportional to the debt above the floor "
             "and below the cap",
          abs((s5 - s3) - 2.0 * gain) < 1e-6,
          "d3=%.2f d5=%.2f gain=%.2f" % (s3, s5, gain))
    check(2, "the floor binds at small debts (never slower than the hold)",
          abs(s0 - s1) < 1e-9
          and abs(s1 - TUNERS.tes_depth_authority_min_speed_mps) < 1e-9,
          "d0=%.2f d1=%.2f floor=%.2f"
          % (s0, s1, TUNERS.tes_depth_authority_min_speed_mps))
    check(2, "huge debt is CAPPED at the unified defensive ceiling",
          abs(s99 - TUNERS.max_defensive_speed_mps) < 1e-9,
          "speed=%.2f ceiling=%.2f" % (s99, TUNERS.max_defensive_speed_mps))

    # ── TARGET-MOTION FEED-FORWARD ──
    moving = stub_world(carrier_vx=3.0)
    sm0 = tda.tes_depth_convergence_mps(moving, d, 0.0, TUNERS)
    check(2, "the target's own motion is fed forward (tracking ON default)",
          abs(sm0 - 3.0) < 1e-6, "speed=%.2f" % sm0)

    no_track = SimpleNamespace()
    for attr in ("tes_depth_authority_gain_per_s",
                 "tes_depth_authority_min_speed_mps",
                 "tes_depth_authority_max_speed_mps",
                 "tes_depth_authority_track_target",
                 "max_defensive_speed_mps"):
        setattr(no_track, attr, getattr(TUNERS, attr))
    no_track.tes_depth_authority_track_target = False
    sn0 = tda.tes_depth_convergence_mps(moving, d, 0.0, no_track)
    check(2, "...and is disableable: track OFF => PURE proportional law",
          abs(sn0 - no_track.tes_depth_authority_min_speed_mps) < 1e-9,
          "speed=%.2f" % sn0)

    # ── GAIN IS THE SCALING KNOB ──
    hot = SimpleNamespace()
    for attr in ("tes_depth_authority_min_speed_mps",
                 "tes_depth_authority_max_speed_mps",
                 "tes_depth_authority_track_target",
                 "max_defensive_speed_mps"):
        setattr(hot, attr, getattr(TUNERS, attr))
    hot.tes_depth_authority_gain_per_s = TUNERS.tes_depth_authority_gain_per_s * 2.0
    hot_s = tda.tes_depth_convergence_mps(world, d, 1.0, hot)
    base_s = tda.tes_depth_convergence_mps(world, d, 1.0, TUNERS)
    check(2, "doubling the gain raises the commanded closure speed",
          hot_s > base_s, "base=%.2f hot=%.2f" % (base_s, hot_s))

    # ── MALFORMED INPUTS DEGRADE SAFELY ──
    junk = SimpleNamespace(
        tes_depth_authority_gain_per_s="junk",
        tes_depth_authority_min_speed_mps="junk",
        tes_depth_authority_max_speed_mps="junk",
        max_defensive_speed_mps="junk",
    )
    j = tda.tes_depth_convergence_mps(world, d, 2.0, junk)
    check(2, "malformed tuner values degrade to finite module defaults",
          math.isfinite(j) and j > 0.0, "speed=%.2f" % j)
    check(2, "negative debt is clamped to zero (never negative speed)",
          tda.tes_depth_convergence_mps(world, d, -5.0, TUNERS) >= 0.0)
    check(2, "a None world is safe (no carrier => no feed-forward)",
          abs(tda.tes_depth_convergence_mps(None, d, 1.0, TUNERS)
              - tda.tes_depth_convergence_mps(world, d, 1.0, TUNERS)) < 1e-9)


# ─────────────────────────────────────────────────────────────────────
# [3] THE WRITE SET
# ─────────────────────────────────────────────────────────────────────

def verify_write_set():
    section("[3] The write set — exactly four per-frame fields, nothing else")

    world = stub_world(carrier_vx=2.0)
    target = 25.0
    d = stub_defender(x=40.0, target=target)
    before = dict(vars(d))

    granted = tda.apply_tes_depth_authority(world, [d], TUNERS)

    check(3, "the writer returns the granted body (wiring proof)",
          granted == [d])
    check(3, "target_x is the canonical Stage-1 role depth target",
          abs(float(d.target_x) - target) < 1e-9,
          "tx=%.2f target=%.2f" % (d.target_x, target))
    check(3, "move_speed is the gap-proportional convergence speed",
          abs(float(d.move_speed)
              - tda.tes_depth_convergence_mps(
                  world, d, tdo.tes_depth_debt_m(world, d, target_x=target),
                  TUNERS)) < 1e-6,
          "speed=%.2f" % d.move_speed)
    check(3, "movement_state is the authority's accel-lerp selector "
             "(NOT the smooth-damp DRIFT port)",
          d.movement_state is tda.TES_DEPTH_AUTHORITY_MOVE_STATE
          and d.movement_state is not MovementState.DRIFT,
          "state=%s" % getattr(d.movement_state, "name", d.movement_state))
    check(3, "target_source is the authority's registered stamp",
          d.target_source == tda.TES_DEPTH_AUTHORITY_SOURCE)

    changed = {k for k in vars(d) if vars(d)[k] != before.get(k)}
    unexpected = changed - set(ALLOWED_WRITES)
    check(3, "ONLY the four allowed fields changed",
          not unexpected, "unexpected=%s" % sorted(unexpected))

    for attr in FORBIDDEN_WRITES:
        check(3, "%s is never written" % attr,
              vars(d).get(attr) == before.get(attr),
              "before=%r after=%r" % (before.get(attr), vars(d).get(attr)))

    # ── A NON-OBLIGATED BODY IS WRITTEN NOTHING ──
    aligned = stub_defender(pid=2, x=25.0, target=25.0)
    untouched = dict(vars(aligned))
    granted2 = tda.apply_tes_depth_authority(world, [aligned], TUNERS)
    check(3, "an in-tolerance body is NOT granted",
          granted2 == [])
    check(3, "an in-tolerance body is written NOTHING "
             "(a compliant frame is byte-identical)",
          dict(vars(aligned)) == untouched)

    # ── A FROZEN OBLIGATED BODY IS WRITTEN NOTHING ──
    frozen = stub_defender(pid=3, x=40.0, target=25.0, frozen=True)
    frozen_before = dict(vars(frozen))
    granted3 = tda.apply_tes_depth_authority(world, [frozen], TUNERS)
    check(3, "a frozen obligated body is NOT granted (freeze contract)",
          granted3 == [])
    check(3, "...and is written NOTHING",
          dict(vars(frozen)) == frozen_before)

    # ── EXEMPT / OUT-OF-CHAMBER BODIES ──
    marker = stub_defender(pid=4, role="MARKER_A", x=40.0, target=25.0)
    m_before = dict(vars(marker))
    check(3, "an exempt body is never granted",
          tda.apply_tes_depth_authority(world, [marker], TUNERS) == []
          and dict(vars(marker)) == m_before)

    op = stub_world(phase="open_play")
    od = stub_defender(pid=5, x=40.0, target=25.0)
    o_before = dict(vars(od))
    check(3, "outside the chamber nothing is granted or written",
          tda.apply_tes_depth_authority(op, [od], TUNERS) == []
          and dict(vars(od)) == o_before)

    # ── SAFE ON EMPTY / NONE INPUT ──
    check(3, "None world returns an empty grant list",
          tda.apply_tes_depth_authority(None, [d], TUNERS) == [])
    check(3, "empty / None defender list is safe",
          tda.apply_tes_depth_authority(world, [], TUNERS) == []
          and tda.apply_tes_depth_authority(world, [None], TUNERS) == [])

    # ── NO TARGET RESOLVABLE => NO GRANT ──
    notarget = stub_defender(pid=6, x=40.0, target=None)
    notarget.role_depth_target_x = None
    check(3, "a body with no resolvable target is not granted",
          tda.apply_tes_depth_authority(
              stub_world(carrier_vx=0.0, with_carrier=False),
              [notarget], TUNERS) == [])

    # ── DIAGNOSTIC REPORT ──
    rep = tda.format_depth_authority_report(world, [d], TUNERS)
    check(3, "the diagnostic report names the granted body",
          "id=%s" % d.player_id in rep, rep[:70])
    check(3, "the report is clear when nobody owes depth",
          "clear" in tda.format_depth_authority_report(
              world, [stub_defender(x=25.0, target=25.0)], TUNERS))


# ─────────────────────────────────────────────────────────────────────
# [4] THE GATE
# ─────────────────────────────────────────────────────────────────────

def verify_gate():
    section("[4] The gate — tuner + runtime lever, safe on a bare namespace")

    check(4, "the authority is armed by default",
          tda.depth_authority_enabled(TUNERS) is True)
    off = SimpleNamespace(tes_depth_authority_enabled=False)
    check(4, "the tuner can disable it",
          tda.depth_authority_enabled(off) is False)
    check(4, "an absent tuner attribute defaults to ARMED",
          tda.depth_authority_enabled(SimpleNamespace()) is True)
    check(4, "a None tuner defaults to ARMED",
          tda.depth_authority_enabled(None) is True)

    world = stub_world()
    d = stub_defender(x=40.0, target=25.0)
    before = dict(vars(d))
    check(4, "a disabled authority grants nothing and writes nothing",
          tda.apply_tes_depth_authority(world, [d], off) == []
          and dict(vars(d)) == before)

    # ── RUNTIME LEVER ──
    prev = os.environ.get("RLM_TES_DEPTH_AUTHORITY")
    os.environ["RLM_TES_DEPTH_AUTHORITY"] = "0"
    try:
        check(4, "RLM_TES_DEPTH_AUTHORITY=0 disarms the authority",
              tda.depth_authority_enabled(TUNERS) is False)
        check(4, "...and the writer then grants nothing",
              tda.apply_tes_depth_authority(world, [d], TUNERS) == [])
    finally:
        if prev is None:
            os.environ.pop("RLM_TES_DEPTH_AUTHORITY", None)
        else:
            os.environ["RLM_TES_DEPTH_AUTHORITY"] = prev

    check(4, "the lever is restored (authority armed again)",
          tda.depth_authority_enabled(TUNERS) is True)


# ─────────────────────────────────────────────────────────────────────
# [5] NO INVENTED STATE
# ─────────────────────────────────────────────────────────────────────

FORBIDDEN_PREFIXES = ("stamp_", "clear_", "write_", "set_", "arm_", "latch_")
FORBIDDEN_NAMES = (
    "AUTHORITY_ATTR",
    "AUTHORITY_TARGET_ATTR",
    "AUTHORITY_ACTIVE_ATTR",
    "AUTHORITY_REARM_COOLDOWN_FRAMES",
    "AUTHORITY_HYSTERESIS_M",
    "AUTHORITY_TARGETS",
)
FORBIDDEN_SOURCE_TOKENS = (
    "POST_RUCK_DURATION_S",
    "phase_timer",
    "set_phase",
    "release_controller",
)
# Attribute names that would be a latch / cooldown / target store if any
# Store node wrote them inside this module.
FORBIDDEN_STORE_ATTRS = (
    "tes_depth_authority_active",
    "tes_depth_authority_pending",
    "tes_depth_authority_cooldown",
    "tes_depth_authority_target_x",
    "tes_depth_authority_frames",
)


def verify_no_invented_state():
    section("[5] No invented state — no latch / hysteresis / cooldown / "
            "target store")

    public = [n for n in dir(tda) if not n.startswith("__")]
    bad_prefix = [n for n in public
                  if any(n.startswith(p) for p in FORBIDDEN_PREFIXES)]
    check(5, "no stamp_/clear_/write_/set_/arm_/latch_ API is defined",
          not bad_prefix, "offenders=%s" % bad_prefix)

    bad_names = [n for n in FORBIDDEN_NAMES if n in public]
    check(5, "no latch / cooldown / target-store name is defined",
          not bad_names, "offenders=%s" % bad_names)

    src = read_src(MODULE_PATH)
    bad_tokens = [t for t in FORBIDDEN_SOURCE_TOKENS if t in src]
    check(5, "the module touches no phase timer / release constant",
          not bad_tokens, "offenders=%s" % bad_tokens)

    # ── NO Store TO A LATCH-LIKE NAME ANYWHERE IN THE MODULE ──
    tree = ast.parse(src)
    bad_stores = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if node.attr in FORBIDDEN_STORE_ATTRS:
                bad_stores.append(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id in FORBIDDEN_STORE_ATTRS:
                bad_stores.append(node.id)
    check(5, "no Store to a latch / cooldown / target-store attribute",
          not bad_stores, "offenders=%s" % bad_stores)

    # ── THE AUTHORITY IS A PURE FUNCTION (no hidden state) ──
    world = stub_world(carrier_vx=1.5)
    a = stub_defender(pid=10, x=40.0, target=25.0)
    b = stub_defender(pid=11, x=40.0, target=25.0)
    g1 = tda.apply_tes_depth_authority(world, [a], TUNERS)
    snap1 = (a.target_x, a.move_speed, a.target_source)
    g2 = tda.apply_tes_depth_authority(world, [a], TUNERS)
    snap2 = (a.target_x, a.move_speed, a.target_source)
    check(5, "two identical frames produce identical grants",
          len(g1) == len(g2) == 1)
    check(5, "two identical frames produce identical commands",
          snap1 == snap2, "%r vs %r" % (snap1, snap2))

    # ── NOTHING PERSISTS ON THE WORLD ──
    w_before = dict(vars(world))
    tda.apply_tes_depth_authority(world, [b], TUNERS)
    check(5, "the authority writes NOTHING onto the world",
          dict(vars(world)) == w_before,
          "added=%s" % sorted(set(vars(world)) - set(w_before)))


# ─────────────────────────────────────────────────────────────────────
# [6] SINGLE SOURCE + WIRING
# ─────────────────────────────────────────────────────────────────────

def _imports_module(source, name):
    """True when *source* really IMPORTS *name* (AST, not a substring)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == name:
                return True
            for alias in node.names:
                if alias.name == name:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == name:
                    return True
    return False


def _engine_importers(module_name):
    offenders = []
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
                src = read_src(path)
                if _imports_module(src, module_name):
                    offenders.append(path)
    return offenders


def verify_single_source_and_wiring():
    section("[6] Single source + wiring")

    # ── THE CHAMBER IS STAGE 1'S, NOT A COPY ──
    check(6, "TES_DEPTH_AUTHORITY_PHASES IS the Stage-1 chamber (identity)",
          tda.TES_DEPTH_AUTHORITY_PHASES is tdo.TES_DEPTH_OBLIGATION_PHASES,
          "phases=%s" % sorted(tda.TES_DEPTH_AUTHORITY_PHASES))
    check(6, "the chamber is exactly the four TES phases",
          set(tda.TES_DEPTH_AUTHORITY_PHASES) == set(CHAMBER_PHASES))

    # ── THE TARGET AND THE DEBT ARE STAGE 1'S ──
    src = read_src(MODULE_PATH)
    check(6, "the module imports the Stage-1 obligation module",
          _imports_module(src, "tes_depth_obligation"))
    for fn in ("is_tes_depth_obligated", "tes_depth_target_x",
               "tes_depth_debt_m"):
        check(6, "the authority consumes Stage-1 %s" % fn, fn in src)
    check(6, "the module defines no second copy of the debt or the target",
          "def tes_depth_debt_m" not in src
          and "def tes_depth_target_x" not in src)

    # ── WIRING: WRITTEN WHERE THE TES CHAMBER LIVES ──
    mgr = read_src(MANAGER_SRC)
    check(6, "defence_manager imports the authority",
          _imports_module(mgr, MODULE_NAME))
    i_hold = mgr.find("_held = apply_tes_drive_structure(")
    i_auth = mgr.find("apply_tes_depth_authority(")
    i_integ = mgr.find("PLAYER.UPDATE IS THE SOLE INTEGRATOR")
    located = [i_hold, i_auth, i_integ]
    check(6, "the TES drive frame-contract anchors are all located",
          all(i != -1 for i in located), "indices=%s" % located)
    check(6, "the authority runs AFTER the structure hold",
          0 <= i_hold < i_auth)
    check(6, "the authority runs BEFORE the single integration loop",
          0 <= i_auth < i_integ)
    check(6, "the granted bodies are exposed as the wiring proof",
          "_tes_depth_authority_last" in mgr)

    # ── THE target_source IS REGISTERED IN THE CONDUCTOR MAP ──
    guard = read_src(GUARD_SRC)
    check(6, "the authority's target_source is registered for TES phases",
          '"%s"' % tda.TES_DEPTH_AUTHORITY_SOURCE in guard)

    # ── NO MODULE-SCOPE IMPORT CYCLE ──
    tree = ast.parse(src)
    module_scope_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_scope_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_scope_imports.append(node.module or "")
    allowed = ("__future__", "math", "typing", "player.movement_states",
               "game.defence.defence_lvl_1.tes_depth_obligation")
    offenders = [m for m in module_scope_imports
                 if not m.startswith(allowed)]
    check(6, "module-scope imports are the safe set only (no cycle)",
          not offenders, "offenders=%s" % offenders)

    # ── THE AUTHORITY IS WIRED INTO EXACTLY ONE ENGINE MODULE ──
    importers = _engine_importers(MODULE_NAME)
    norm = {os.path.normpath(p) for p in importers}
    check(6, "defence_manager is the ONLY engine importer",
          norm == {os.path.normpath(MANAGER_SRC)},
          "importers=%s" % sorted(norm))

    # ── STAGE 1 REMAINS MEASUREMENT-ONLY (the successor did not wire it) ──
    obl_importers = _engine_importers("tes_depth_obligation")
    # The ONLY engine-side importer of Stage 1 must be its Stage-2
    # successor (the authority reads the obligation); anything else would
    # mean Stage 1 had been wired into behaviour, which Stage 1 forbids.
    check(6, "Stage 1's obligation module is imported ONLY by its Stage-2 "
             "successor",
          all(os.path.basename(p) == "tes_depth_authority.py"
              for p in obl_importers),
          "importers=%s" % sorted(obl_importers))
    check(6, "the Stage-1 source is unchanged in spirit (no authority added)",
          "def apply_" not in read_src(OBLIGATION_SRC))


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("TES DEPTH AUTHORITY CONTRACT VERIFICATION")
    print("(Stage 2: the depth-discharge successor — no invented state)")
    print("=" * 72)

    verify_verdict()
    verify_convergence_law()
    verify_write_set()
    verify_gate()
    verify_no_invented_state()
    verify_single_source_and_wiring()

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
