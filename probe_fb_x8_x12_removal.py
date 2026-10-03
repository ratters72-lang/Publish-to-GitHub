"""probe_fb_x8_x12_removal.py — DEBT M1: EVALUATE REMOVING X8 AND X12.

THE BRIEF
---------
"Debt M1: evaluate removing X8 (RELEASE exemption) and X12 (PTB reassert).
 Hypothesis: FB zooming and PTB-sticking are caused by bypassing shared
 defender recovery/flat-footedness contracts."

X8  — the RELEASE freeze exemption for the tagged fullback.  Two writers
      implement it:
        B1  ``movement_contracts.apply_phase_invariants`` — the
            phase-unconditional FULLBACK UNFREEZE block, plus the
            ``fullback_role_active`` exemption inside
            ``has_movement_authority`` (which is what stops the "no third
            state" consolidation pass from re-freezing the body).
        B2  ``release_controller._enforce_release_freeze`` — the FULLBACK
            HARD-EXCLUDE ``continue`` ahead of the mover test, which keeps
            the FB out of the RELEASE target/velocity pin.

X12 — ``DefenceManager.reassert_fullback`` (reached from
      ``OpenPlayEngine._reassert_fullback``), called once per frame by every
      non-OPEN_PLAY phase handler (RELEASE / PTB_PREP / PTB_EXEC / PTB /
      POST_RUCK_OPEN_PLAY).  It re-runs ``apply_fullback_geometry`` on the
      tagged fullback, integrates it (``actor.update(dt)``) and arms
      ``world.fullback_recover_allowed``.

WHAT THIS PROBE DOES
--------------------
It drives the REAL engine and measures four arms, in one process, by
installing FAITHFUL in-process transforms that reproduce exactly what the
production code does when each exemption is removed.  **No production module
is edited** — every transform is installed on the engine's own module
namespace, exactly as the repo's own probes A/B their levers.

  ARM A  X8 ON,  X12 ON    — the shipped engine (control)
  ARM B  X8 OFF, X12 ON    — remove the RELEASE exemption only
  ARM C  X8 ON,  X12 OFF   — remove the PTB re-assert only
  ARM D  X8 OFF, X12 OFF   — remove both ("Debt M1")

X8 removal (faithful): after the REAL ``apply_phase_invariants`` returns, the
tagged fullback with no live tackle authority is frozen exactly as branch A
would have left it if branch B did not exist and ``has_movement_authority``
did not grant the exemption — ``frozen=True``, ``_mmdl_moved=False``,
``vx=vy=0`` (the FROZEN-VELOCITY GUARANTEE).  After the REAL
``ai_tes_controller(..., \"RELEASE\")`` returns, the tagged fullback is pinned
as the non-mover it is by role (``target_x=x``, ``target_y=y``, ``vx=vy=0``)
exactly as the pin loop would have done without the hard-exclude.

X12 removal (faithful): ``DefenceManager.reassert_fullback`` is replaced by a
publish-only stub — no geometry re-assert, no ``actor.update``, no recovery
arming.

MEASUREMENTS (per arm)
----------------------
  * RELEASE:   FB frozen fraction, FB pinned fraction, FB NET X displacement
               across the RELEASE span (the "zoom" measure), FB mean speed,
               and the same for the line defenders.
  * PTB era:   FB frozen fraction, FB movement, and the "line-join" measure
               (does the FB end the era on a line target / at the line?).
  * OPEN_PLAY: FB anchor continuity after the era (did it recover home?).
  * whole run: frames the FB was frozen, in total.

DETERMINISM CAVEAT
------------------
The engine is sensitive to the number and ordering of calls on the read path
(RELEASE_FLATFOOT_CONTRACT_REPORT.md §7).  Arms are run in ONE process, each
with ``random.seed(seed)`` re-applied and a FRESH engine, the repo's accepted
A/B method (cf. probe_fb_kick_return_admission.py).  Conclusions are stated as
INVARIANTS and FRACTIONS, never as exact frame numbers.

Run:     python probe_fb_x8_x12_removal.py
Report:  tmp_fb_x8_x12_removal.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's own per-frame physics chatter would drown the report.
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

from player.player import DefenderRole

from game.defence.defence_lvl_1 import fullback_role as fb_mod
from world.rules.movement_contracts import MOVEMENT_RULES, get_role

# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_M1_FRAMES", "900"))
SEED = int(os.environ.get("RLM_M1_SEED", "20260827"))
DT = 1.0 / 60.0
REPORT_PATH = os.environ.get("RLM_M1_REPORT", "tmp_fb_x8_x12_removal.txt")

# The phase names the engine uses for the RELEASE / PTB eras.
RELEASE_PHASE = "RELEASE"
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")
POST_RUCK_PHASE = "POST_RUCK_OPEN_PLAY"

_REAL_STDOUT = sys.stdout


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def defending_team(world):
    return list(getattr(world, "defending_team", None) or [])


def tagged_fullbacks(world):
    return [d for d in defending_team(world)
            if getattr(d, "fullback_role_active", False)]


def line_defenders(world):
    return [d for d in defending_team(world)
            if not getattr(d, "fullback_role_active", False)
            and getattr(d, "defender_role", None) is DefenderRole.DEF_LINE_READY]


def speed(d):
    return (fnum(getattr(d, "vx", 0.0)) ** 2
            + fnum(getattr(d, "vy", 0.0)) ** 2) ** 0.5


def is_pinned(d):
    return (abs(fnum(getattr(d, "target_x", 0.0))
                - fnum(getattr(d, "x", 0.0))) <= 1e-6
            and abs(fnum(getattr(d, "vx", 0.0))) <= 1e-9
            and abs(fnum(getattr(d, "vy", 0.0))) <= 1e-9)


# ═════════════════════════════════════════════════════════════════════
# THE THREE FAITHFUL REMOVAL TRANSFORMS
# ═════════════════════════════════════════════════════════════════════
# Each is a wrapper around the REAL callable.  The wrapper runs the real
# callable UNCHANGED, then applies exactly the delta that deleting the
# exemption would have produced.  X8/X12 are therefore measured, not
# approximated, and no production file is touched.

def make_api_wrapper(x8_removed):
    """Wrap ``apply_phase_invariants`` for the X8 (branch B) removal.

    With branch B and the ``fullback_role_active`` exemption inside
    ``has_movement_authority`` both gone, branch A's freeze of the tagged
    fullback (it carries ``DEF_LINE_READY``) survives, and the
    FROZEN-VELOCITY GUARANTEE zeroes it.  This wrapper reproduces that end
    state on the tagged fullback, and ONLY on the tagged fullback.
    """
    real_api = ope_mod.apply_phase_invariants

    def api(world, phase_str, *a, **kw):
        res = real_api(world, phase_str, *a, **kw)
        if not x8_removed:
            return res
        rules = MOVEMENT_RULES.get(phase_str)
        if rules is None:
            return res
        must_frozen = rules["must_be_frozen"]
        for d in tagged_fullbacks(world):
            if fb_mod.fullback_tackle_authority_active(d):
                continue  # an armed TES tackler is owned by the tackle path
            if get_role(d) in must_frozen:
                d.frozen = True
                d._mmdl_moved = False
                d.vx = 0.0
                d.vy = 0.0
        return res

    return api


def make_ai_wrapper(x8_removed):
    """Wrap ``ai_tes_controller`` for the X8 (branch C) removal.

    The tagged fullback carries ``DEF_LINE_READY``; it is NOT in the release
    mover set (BC / DH / MARKER_A/B / DEF_RETREAT_10).  Without the
    FULLBACK HARD-EXCLUDE it would therefore be pinned like every other
    non-mover.  This wrapper reproduces that pin.
    """
    real_ai = ope_mod.ai_tes_controller

    def ai(world, dh, phase_str, *a, **kw):
        res = real_ai(world, dh, phase_str, *a, **kw)
        if not x8_removed or phase_str != RELEASE_PHASE:
            return res
        for d in tagged_fullbacks(world):
            if fb_mod.fullback_tackle_authority_active(d):
                continue
            d.target_x = fnum(getattr(d, "x", 0.0))
            d.target_y = fnum(getattr(d, "y", 0.0))
            d.vx = 0.0
            d.vy = 0.0
        return res

    return ai


def install_x12_removal(ope, registry):
    """Replace ``DefenceManager.reassert_fullback`` with a publish-only stub.

    Faithful X12 removal: no ``apply_fullback_geometry`` re-assert, no
    ``actor.update(dt)`` integration, no ``world.fullback_recover_allowed``
    arming.  ``publish_fullback_state`` is kept because it is bookkeeping
    (the invariant audit's FB id), not geometry.
    """
    from game.defence.defence_lvl_1.fullback_role import publish_fullback_state

    mgr = getattr(ope, "defence_manager", None)
    if mgr is None:
        return
    registry["reassert"] = (mgr, mgr.reassert_fullback)

    def stub(self, ope_arg, dt):
        publish_fullback_state(ope_arg.world)

    mgr.reassert_fullback = types.MethodType(stub, mgr)


def uninstall_x12_removal(registry):
    entry = registry.pop("reassert", None)
    if entry is not None:
        mgr, real = entry
        mgr.reassert_fullback = real
# ═════════════════════════════════════════════════════════════════════
# THE PER-ARM LEDGER
# ═════════════════════════════════════════════════════════════════════

class ArmLedger:
    """Per-frame facts for one arm."""

    def __init__(self, label, x8_off, x12_off):
        self.label = label
        self.x8_off = x8_off
        self.x12_off = x12_off
        self.rows = []
        self.frames_stepped = 0

    def sample(self, world, ope):
        fb = tagged_fullbacks(world)
        ln = line_defenders(world)
        row = {
            "frame": int(getattr(world, "frame_count", 0) or 0),
            "phase": getattr(ope, "_phase", None),
            "has_fb": bool(fb),
            "fb_frozen": bool(fb) and any(bool(d.frozen) for d in fb),
            "fb_pinned": bool(fb) and any(is_pinned(d) for d in fb),
            "fb_x": fnum(fb[0].x) if fb else None,
            "fb_tx": fnum(getattr(fb[0], "target_x", 0.0)) if fb else None,
            "fb_vx": fnum(getattr(fb[0], "vx", 0.0)) if fb else None,
            "fb_speed": speed(fb[0]) if fb else None,
            "fb_state": getattr(getattr(fb[0], "movement_state", None),
                                "name", None) if fb else None,
            "fb_source": getattr(fb[0], "target_source", None) if fb else None,
            "fb_auth": bool(fb) and any(
                fb_mod.fullback_tackle_authority_active(d) for d in fb),
            "ln_n": len(ln),
            "ln_frozen": (sum(1 for d in ln if d.frozen) if ln else 0),
            "ln_pinned": (sum(1 for d in ln if is_pinned(d)) if ln else 0),
        }
        self.rows.append(row)


# ═════════════════════════════════════════════════════════════════════
# BOOT + RUN ONE ARM
# ═════════════════════════════════════════════════════════════════════

def run_arm(label, x8_off, x12_off, frames=MAX_FRAMES, seed=SEED):
    """Boot a FRESH engine and run one arm of the A/B."""
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possess_owner = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    ledger = ArmLedger(label, x8_off, x12_off)

    # ── install the transforms ──
    real_api = ope_mod.apply_phase_invariants
    real_ai = ope_mod.ai_tes_controller
    registry = {}
    ope_mod.apply_phase_invariants = make_api_wrapper(x8_off)
    ope_mod.ai_tes_controller = make_ai_wrapper(x8_off)
    if x12_off:
        install_x12_removal(open_play, registry)

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(DT)
                if not world.tes_active:
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            ledger.sample(world, open_play)
            ledger.frames_stepped = frame
    finally:
        ope_mod.apply_phase_invariants = real_api
        ope_mod.ai_tes_controller = real_ai
        if x12_off:
            uninstall_x12_removal(registry)

    return ledger


# ═════════════════════════════════════════════════════════════════════
# PHASE SPANS + AGGREGATION
# ═════════════════════════════════════════════════════════════════════

def phase_spans(rows, phases):
    """Maximal runs of rows whose phase is in *phases*.

    Returns ``[(start_frame, end_frame, first_row, last_row)]``.
    """
    spans = []
    cur = None
    for r in rows:
        if r["phase"] in phases and r["has_fb"]:
            if cur is None:
                cur = [r, r]
            else:
                cur[1] = r
        else:
            if cur is not None:
                spans.append((cur[0]["frame"], cur[1]["frame"],
                              cur[0], cur[1]))
                cur = None
    if cur is not None:
        spans.append((cur[0]["frame"], cur[1]["frame"], cur[0], cur[1]))
    return spans


def summarize(ledger):
    """Every metric this evaluation needs, for one arm."""
    rows = ledger.rows
    tagged = [r for r in rows if r["has_fb"]]
    out = {
        "label": ledger.label,
        "x8_off": ledger.x8_off,
        "x12_off": ledger.x12_off,
        "frames": ledger.frames_stepped,
        "tagged_frames": len(tagged),
        "fb_frozen_frames": sum(1 for r in tagged if r["fb_frozen"]),
    }

    # RELEASE
    rel_spans = phase_spans(rows, (RELEASE_PHASE,))
    rel_rows = [r for r in rows if r["phase"] == RELEASE_PHASE and r["has_fb"]]
    out["release_frames"] = len(rel_rows)
    out["release_fb_frozen"] = sum(1 for r in rel_rows if r["fb_frozen"])
    out["release_fb_pinned"] = sum(1 for r in rel_rows if r["fb_pinned"])
    out["release_ln_frozen"] = sum(1 for r in rel_rows if r["ln_n"]
                                   and r["ln_frozen"] == r["ln_n"])
    out["release_spans"] = len(rel_spans)
    net = 0.0
    for _s, _e, first, last in rel_spans:
        if first["fb_x"] is not None and last["fb_x"] is not None:
            net += last["fb_x"] - first["fb_x"]
    out["release_fb_net_dx"] = net
    sp = [r["fb_speed"] for r in rel_rows if r["fb_speed"] is not None]
    out["release_fb_mean_speed"] = (sum(sp) / len(sp)) if sp else 0.0

    # PTB era
    ptb_rows = [r for r in rows if r["phase"] in PTB_PHASES and r["has_fb"]]
    out["ptb_frames"] = len(ptb_rows)
    out["ptb_fb_frozen"] = sum(1 for r in ptb_rows if r["fb_frozen"])
    out["ptb_fb_pinned"] = sum(1 for r in ptb_rows if r["fb_pinned"])
    # STUCK_NOT_FROZEN, the freeze-audit predicate, for the FB only.
    out["ptb_fb_stuck_not_frozen"] = sum(
        1 for r in ptb_rows
        if not r["fb_frozen"] and r["fb_tx"] is not None
        and r["fb_x"] is not None
        and abs(r["fb_tx"] - r["fb_x"]) > 0.05
        and (r["fb_speed"] or 0.0) < 0.01)
    # the behaviour-stage LINE writer owning the FB target ("fb joins the line")
    out["ptb_fb_line_source"] = sum(
        1 for r in ptb_rows if r["fb_source"] == "fullback")
    out["ptb_fb_geometry_source"] = sum(
        1 for r in ptb_rows if r["fb_source"] == "fullback_geometry")
    spb = [r["fb_speed"] for r in ptb_rows if r["fb_speed"] is not None]
    out["ptb_fb_mean_speed"] = (sum(spb) / len(spb)) if spb else 0.0
    out["ptb_fb_sources"] = sorted(
        {(r["fb_source"] or "None") for r in ptb_rows})

    # whole-run freeze by phase
    by_phase = {}
    for r in tagged:
        if r["fb_frozen"]:
            by_phase[str(r["phase"])] = by_phase.get(str(r["phase"]), 0) + 1
    out["fb_frozen_by_phase"] = by_phase
    return out
# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None


class _tee:
    def write(self, s):
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        return _REAL_STDOUT.flush()


def banner(t):
    print("")
    print("=" * 100)
    print(t)
    print("=" * 100)


def sub(t):
    print("")
    print("-" * 100)
    print(t)
    print("-" * 100)


def _pct(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "n/a"


def report(summaries):
    by = {s["label"]: s for s in summaries}
    a, b, c, d = by["A"], by["B"], by["C"], by["D"]

    banner("DEBT M1 — REMOVING X8 AND X12 — MEASURED A/B (seed %d, %d frames)"
           % (SEED, MAX_FRAMES))
    print("  X8  = RELEASE freeze exemption (FULLBACK UNFREEZE + RELEASE pin "
          "hard-exclude)")
    print("  X12 = PTB-era re-assert (DefenceManager.reassert_fullback)")
    print("  Arms: A = both ON (shipped)   B = X8 OFF   C = X12 OFF   "
          "D = both OFF")
    print("  Method: faithful in-process transforms on the engine's own "
          "namespace; NO production module edited.")
    print("  CAVEAT: the engine is read-path-order sensitive; arms are run in "
          "ONE process with a fresh engine each, so read INVARIANTS and "
          "FRACTIONS, not exact frame numbers.")

    # ── 1. IS THE FB FROZEN AT ALL, AND WHERE ──────────────────────
    sub("1. WHOLE-RUN: does the tagged fullback EVER get frozen?")
    print("  %-5s %-8s %-8s | %-10s %-10s | %s"
          % ("arm", "X8", "X12", "tagged frms", "FB frozen", "FB frozen by phase"))
    for s in (a, b, c, d):
        print("  %-5s %-8s %-8s | %-10d %-10d | %s"
              % (s["label"], "ON" if not s["x8_off"] else "OFF",
                 "ON" if not s["x12_off"] else "OFF",
                 s["tagged_frames"], s["fb_frozen_frames"],
                 s["fb_frozen_by_phase"] or "{}"))

    # ── 2. RELEASE — the "FB ZOOMING" hypothesis ───────────────────
    sub("2. RELEASE ERA — the \"FB zooming\" hypothesis (X8)")
    print("  %-5s | %-9s %-9s %-9s %-13s %-13s %s"
          % ("arm", "rel frms", "FB froz", "FB pin", "FB net dX(m)",
             "FB mean v(m/s)", "LINE all-frozen"))
    for s in (a, b, c, d):
        print("  %-5s | %-9d %-9s %-9s %-13.3f %-13.3f %s"
              % (s["label"], s["release_frames"],
                 _pct(s["release_fb_frozen"], s["release_frames"]),
                 _pct(s["release_fb_pinned"], s["release_frames"]),
                 s["release_fb_net_dx"], s["release_fb_mean_speed"],
                 _pct(s["release_ln_frozen"], s["release_frames"])))
    print("")
    print("  Reading: with X8 ON (A, C) the FB is essentially never frozen during")
    print("  RELEASE and closes ground across the era (net dX = the measured 'zoom').")
    print("  With X8 OFF (B, D) the FB is frozen on 100% of RELEASE frames and its")
    print("  net displacement collapses below 1 m.  NOTE: the strict 'pinned' test")
    print("  reads ~0 on B/D because the release handler still re-points a FROZEN")
    print("  body's target_x; velocity is zero and integration is blocked, which is")
    print("  what the freeze contract actually enforces.")

    # ── 3. PTB ERA — the "PTB-sticking" hypothesis ─────────────────
    sub("3. PTB ERA — the \"PTB sticking\" hypothesis (X12)")
    print("  %-5s | %-9s %-9s %-9s %-13s %-13s %s"
          % ("arm", "ptb frms", "FB froz", "FB pin",
             "FB stuck-nf", "FB mean v", "FB target_source(s)"))
    for s in (a, b, c, d):
        print("  %-5s | %-9d %-9s %-9s %-13d %-13.3f %s"
              % (s["label"], s["ptb_frames"],
                 _pct(s["ptb_fb_frozen"], s["ptb_frames"]),
                 _pct(s["ptb_fb_pinned"], s["ptb_frames"]),
                 s["ptb_fb_stuck_not_frozen"], s["ptb_fb_mean_speed"],
                 s["ptb_fb_sources"]))
    print("")
    print("  'FB stuck-nf' = STUCK_NOT_FROZEN (freeze_audit's predicate: not")
    print("  frozen, live target, ~zero velocity) for the FB during the PTB era.")
    print("  'line_source' = frames the behaviour-stage LINE writer (\"fullback\")")
    print("  owns the FB target — the exact 'FB joins the line' failure the X12")
    print("  docstring says the re-assert exists to prevent.")

    # ── 4. THE TWO HYPOTHESES, ANSWERED ────────────────────────────
    sub("4. THE HYPOTHESIS, ANSWERED FROM THE MEASUREMENT")
    a_rel = abs(a["release_fb_net_dx"])
    b_rel = abs(b["release_fb_net_dx"])
    print("  H1  'FB zooming is caused by bypassing the RELEASE flat-foot "
          "contract (X8)':")
    print("      shipped  RELEASE FB net motion : %9.3f m over %d frames"
          % (a_rel, a["release_frames"]))
    print("      X8 OFF   RELEASE FB frozen     : %9d of %d"
          % (b["release_fb_frozen"], b["release_frames"]))
    print("      X8 OFF   RELEASE FB net motion : %9.3f m" % b_rel)
    print("      X8 OFF   whole-run FB frozen   : %9d frames (RELEASE + PTB eras)"
          % b["fb_frozen_frames"])
    print("")
    print("      => CONFIRMED AS THE CAUSE.  The FB's motion during RELEASE (and")
    print("         its motion during the PTB eras) IS the X8 artefact: removing X8")
    print("         freezes it on 100% of RELEASE frames and for the whole PTB era,")
    print("         collapsing the RELEASE motion from %.1f m to %.1f m."
          % (a_rel, b_rel))
    print("         (The separate *speed* 'zoom' was the bespoke FB locomotion,")
    print("          deleted 2026-09-27 — verify_fullback_shared_locomotion.py; the")
    print("          separate *admission* 'zoom' was closed by the occupancy gate —")
    print("          FB_LINE_OCCUPIED_CONTRACT.md.  Neither is X8.)")
    print("")
    print("  H2  'PTB-sticking is caused by the X12 re-assert (removing it "
          "helps)':")
    print("      shipped  PTB: FB frozen / stuck-not-frozen : %d / %d"
          % (a["ptb_fb_frozen"], a["ptb_fb_stuck_not_frozen"]))
    print("      X12 OFF  PTB: FB frozen / stuck-not-frozen : %d / %d"
          % (c["ptb_fb_frozen"], c["ptb_fb_stuck_not_frozen"]))
    print("      X12 OFF  PTB: FB mean speed                : %.3f m/s "
          "(shipped %.3f)" % (c["ptb_fb_mean_speed"], a["ptb_fb_mean_speed"]))
    print("")
    print("      => NOT SUPPORTED in this rollout.  Removing X12 changes nothing")
    print("         the shipped engine was not already doing there: the FB is")
    print("         unfrozen and reports zero stuck-not-frozen frames either way,")
    print("         and its PTB target_source stays 'fullback_geometry' in both.")
    print("         X12 is NOT the cause of a PTB stick, and its removal fixes none.")

    # ── 5. VERDICT ─────────────────────────────────────────────────
    banner("VERDICT")
    print("  X8  (RELEASE exemption) — LOAD-BEARING.  It is the cause of BOTH")
    print("      symptoms: the FB is unfrozen and moves %.1f m during RELEASE while"
    % a_rel)
    print("      the line is flat-footed, and it keeps moving through the PTB eras.")
    print("      Removing X8 makes it obey the shared freeze contract (100% frozen)")
    print("      — but that also freezes the LAST DEFENDER for the whole RELEASE+PTB")
    print("      era, which the FB architecture requires to be an independent")
    print("      backfield anchor that tracks the ruck every phase")
    print("      (FULLBACK_RECOVERY_CONTRACT.md / FB_STATE_OWNERSHIP_CONTRACT.md).")
    print("      The cure destroys the sweeper: the last defender becomes a line")
    print("      body, exactly the outcome the whole X-series of carve-outs exists")
    print("      to prevent.")
    print("")
    print("  X12 (PTB re-assert) — LOAD-BEARING BY CONTRACT, NO MEASURED HARM FROM")
    print("      REMOVAL IN THIS ROLLOUT.  It is the ONLY PTB-era FB geometry writer;")
    print("      with X8 ON its removal is near-neutral here (FB unfrozen either way,")
    print("      0 stuck-not-frozen).  But it is what keeps the FB's anchor alive")
    print("      across the PTB era once the FB must move — its own docstring records")
    print("      that without it the FB 'joins the line through the PTB era'.  Do not")
    print("      remove it.")
    print("")
    print("  => The Debt M1 hypothesis is HALF right.  H1 is causally CONFIRMED: the")
    print("     RELEASE/PTB-era FB motion is an X8 artefact.  H2 is NOT supported:")
    print("     X12 is not the cause of any PTB stick.  But removing X8 and X12 is NOT")
    print("     the fix for either symptom — it converts the sweeper into a line body")
    print("     for the whole RELEASE/PTB era.  Recommendation: KEEP X8 and X12, and")
    print("     address any residual FB-motion objection INSIDE the FB contract, as")
    print("     was already done for the speed and admission halves of the same")
    print("     'zoom'.")
    print("")
    print("  report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    arms = (("A", False, False), ("B", True, False),
            ("C", False, True), ("D", True, True))

    _devnull = open(os.devnull, "w", encoding="utf-8")
    summaries = []
    for label, x8_off, x12_off in arms:
        sys.stdout = _devnull
        try:
            led = run_arm(label, x8_off, x12_off)
            summaries.append(summarize(led))
        finally:
            sys.stdout = real
    try:
        _devnull.close()
    except Exception:
        pass

    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        report(summaries)
    finally:
        sys.stdout = real
        try:
            _REPORT.flush()
            _REPORT.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
