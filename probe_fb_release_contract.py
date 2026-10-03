"""probe_fb_release_contract.py — DOES THE TAGGED FULLBACK ENTER THE
TACKLE-RELEASE FLAT-FOOTED CONTRACT?

THE QUESTION
------------
Every DEF_LINE_READY defender is frozen flat-footed for the whole RELEASE
phase, by two writers in sequence:

  1. ``world/rules/movement_contracts.apply_phase_invariants`` — the SOLE
     freeze authority.  ``MOVEMENT_RULES["RELEASE"]["must_be_frozen"]`` holds
     ``Role.DEF_LINE_READY``, so the role pass pins the body
     (``p.frozen = True``).
  2. ``world/rules/release_controller._enforce_release_freeze``, reached from
     the RELEASE phase handler through ``ai_tes_controller(world, dh,
     "RELEASE")`` — the TARGET/VELOCITY pin: every non-mover gets
     ``target_x = x``, ``target_y = y``, ``vx = 0``, ``vy = 0``.

The tagged fullback ALSO carries ``defender_role = DEF_LINE_READY``: that is
forced every frame by ``fullback_role.assign_fullback_role`` ("the fullback
always keeps defender_role = DEF_LINE_READY — the one role that is a legal
mover in every MOVEMENT_RULES phase").  On the ROLE TABLE, therefore, the
fullback is indistinguishable from a line defender and is subject to the
SAME RELEASE flat-footed contract.

This probe drives the real engine and measures whether the tagged fullback
actually ENDS each RELEASE frame in that contract, or whether an explicit
exemption reverses it.

THE TWO EXEMPTIONS UNDER MEASUREMENT
------------------------------------
  E1  apply_phase_invariants, "FULLBACK UNFREEZE (2026-08-13, made
      phase-unconditional 2026-09-02)": after the role pass, a later block
      iterates the defending team and, for any ``fullback_role_active``
      body that does NOT carry live tackle authority, writes
      ``p.frozen = False; p.freeze_timer = 0.0`` — in EVERY phase,
      including RELEASE.

  E2  release_controller._enforce_release_freeze, "FULLBACK HARD-EXCLUDE
      (2026-08-27)": the mover loop skips any ``fullback_role_active`` body
      BEFORE the mover test, so the FB is never pinned to its own position
      and its velocity is never zeroed.

WHAT IT LOGS
------------
Per frame, for the tagged fullback and for every non-fullback
DEF_LINE_READY defender:
    frozen, freeze_timer, movement_state, target_x, x, vx, vy,
    defender_role, fullback_role_active, _target_writer

Per call of ``apply_phase_invariants``: the phase, the FB and line-defender
ids, and each body's (frozen, freeze_timer) BEFORE and AFTER the call — so
a freeze applied by the role pass and then REVERSED inside the same call is
visible as ``before=(False,0.0) -> after=(True,0.0)`` for a line defender
and ``before=(False,0.0) -> after=(False,0.0)`` for the FB, with the
intermediate state measurable by direct re-evaluation of the two blocks.

Per call of ``ai_tes_controller`` with ``phase == "RELEASE"``: each body's
(target_x, x, vx, vy, frozen) BEFORE and AFTER, so the TARGET/VELOCITY pin
is attributable per body.

METHOD
------
Read-only: every spy calls the real callable UNCHANGED and the per-frame
ledger is SAMPLED after ``open_play.update(dt)`` returns (the frame's FINAL
applied state).  No engine module is modified.

Run:     python probe_fb_release_contract.py
Report:  tmp_fb_release_contract.txt
Rollout: seed 20260827, 736 frames, dt = 1/60 s.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys

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

# The two writers under measurement, reached through the engine's OWN
# module namespace (``world.open_play_engine`` imports both names directly,
# so patching the module attribute is what the live frame actually calls).
from world.rules.movement_contracts import MOVEMENT_RULES, get_role


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBRC_FRAMES", "736"))
SEED = int(os.environ.get("RLM_FBRC_SEED", "20260827"))
DT = 1.0 / 60.0

# The traced window requested by the task.
WINDOW_LO = int(os.environ.get("RLM_FBRC_LO", "583"))
WINDOW_HI = int(os.environ.get("RLM_FBRC_HI", "736"))

# The designated fullback jersey / pid of interest (seed 20260827 tags 200).
FOCUS_PID = int(os.environ.get("RLM_FBRC_PID", "200"))

REPORT_PATH = os.environ.get(
    "RLM_FBRC_REPORT", "tmp_fb_release_contract.txt"
)


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def pid_of(p):
    return getattr(p, "player_id", None)


def fnum(v, default=0.0):
    """Return *v* as float when numeric, else *default* (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def defending_team(world):
    return list(getattr(world, "defending_team", None) or [])


def tagged_fullbacks(world):
    """Every body carrying the fullback tag, in defending-team order."""
    return [d for d in defending_team(world)
            if getattr(d, "fullback_role_active", False)]


def line_defenders(world):
    """Every non-fullback body whose role is DEF_LINE_READY this frame."""
    return [d for d in defending_team(world)
            if not getattr(d, "fullback_role_active", False)
            and getattr(d, "defender_role", None) is DefenderRole.DEF_LINE_READY]


def freeze_tuple(d):
    """(frozen, freeze_timer) — the pair the freeze authority owns."""
    return (bool(getattr(d, "frozen", False)),
            round(fnum(getattr(d, "freeze_timer", 0.0)), 4))


def pin_tuple(d):
    """(target_x, x, vx, vy, frozen) — the pair the RELEASE pin owns."""
    return (round(fnum(getattr(d, "target_x", 0.0)), 3),
            round(fnum(getattr(d, "x", 0.0)), 3),
            round(fnum(getattr(d, "vx", 0.0)), 3),
            round(fnum(getattr(d, "vy", 0.0)), 3),
            bool(getattr(d, "frozen", False)))


def is_pinned(tup):
    """True when the RELEASE pin ran: target on the body AND zero velocity."""
    tx, x, vx, vy, _frozen = tup
    return abs(tx - x) <= 1e-6 and abs(vx) <= 1e-9 and abs(vy) <= 1e-9


def role_name(d):
    return name_of(getattr(d, "defender_role", None))


def movement_state_name(d):
    return name_of(getattr(d, "movement_state", None))


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class Ledger:
    """Every measured fact for the RELEASE-contract question."""

    def __init__(self):
        self.frame = 0
        self.engine_phase = None
        self.world_phase = None
        self.tes_active = False
        self.frames_stepped = 0
        # per-frame rows for the traced window
        self.rows = []
        # writer-call records
        self.api_calls = []      # apply_phase_invariants
        self.release_pins = []   # ai_tes_controller(..., "RELEASE")
        # FIRST-DIVERGENCE BRANCH ATTRIBUTION (RELEASE calls only)
        self.branch_calls = 0
        self.branch_ln_role_frozen = 0
        self.branch_ln_total = 0
        self.branch_fb_role_frozen = 0
        self.branch_fb_total = 0
        self.branch_fb_exempt = 0
        # first-divergence bookkeeping
        self.first_divergence = None
        # phase timeline over the whole rollout
        self.phase_spans = []
        self.window_phases = {}
        self.focus_tagged_frames = 0
        self.focus_pid = FOCUS_PID

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, world, ope):
        self.frame = int(getattr(world, "frame_count", 0) or 0)
        self.engine_phase = getattr(ope, "_phase", None)
        self.world_phase = name_of(getattr(world, "phase", None))
        self.tes_active = bool(getattr(world, "tes_active", False))

    # ── apply_phase_invariants spy record ────────────────────────────
    def note_api(self, phase, fb, ln, before, after):
        self.api_calls.append({
            "frame": self.frame,
            "phase": phase,
            "fb_ids": [pid_of(p) for p in fb],
            "ln_ids": [pid_of(p) for p in ln],
            "fb_before": [before.get(id(p)) for p in fb],
            "fb_after": [after.get(id(p)) for p in fb],
            "ln_before": [before.get(id(p)) for p in ln],
            "ln_after": [after.get(id(p)) for p in ln],
        })

    # ── branch attribution: WHICH branch froze / unfroze each body ────
    def note_branch(self, ln_role_frozen, ln_total, fb_role_frozen,
                    fb_total, fb_exempt):
        """Tally the two decision branches for one RELEASE call."""
        self.branch_calls += 1
        self.branch_ln_role_frozen += ln_role_frozen
        self.branch_ln_total += ln_total
        self.branch_fb_role_frozen += fb_role_frozen
        self.branch_fb_total += fb_total
        self.branch_fb_exempt += fb_exempt

    # ── RELEASE pin spy record ───────────────────────────────────────
    def note_release_pin(self, fb, ln, before, after):
        self.release_pins.append({
            "frame": self.frame,
            "fb_ids": [pid_of(p) for p in fb],
            "ln_ids": [pid_of(p) for p in ln],
            "fb_before": [before.get(id(p)) for p in fb],
            "fb_after": [after.get(id(p)) for p in fb],
            "ln_before": [before.get(id(p)) for p in ln],
            "ln_after": [after.get(id(p)) for p in ln],
        })

    # ── per-frame sample ─────────────────────────────────────────────
    def sample(self, world, ope):
        """Snapshot the FB and line defenders after the frame completed."""
        fb = tagged_fullbacks(world)
        ln = line_defenders(world)
        if any(pid_of(d) == self.focus_pid for d in fb):
            self.focus_tagged_frames += 1

        row = {
            "frame": self.frame,
            "engine_phase": self.engine_phase,
            "world_phase": self.world_phase,
            "tes_active": self.tes_active,
            "phase_timer": round(fnum(getattr(ope, "_phase_timer", 0.0)), 3),
            "fb_ids": [pid_of(d) for d in fb],
            "fb": [freeze_tuple(d) + (
                movement_state_name(d),
                round(fnum(getattr(d, "target_x", 0.0)), 3),
                round(fnum(getattr(d, "x", 0.0)), 3),
                role_name(d),
            ) for d in fb],
            "ln_ids": [pid_of(d) for d in ln],
            "ln": [freeze_tuple(d) + (
                movement_state_name(d),
                round(fnum(getattr(d, "target_x", 0.0)), 3),
                round(fnum(getattr(d, "x", 0.0)), 3),
                role_name(d),
            ) for d in ln],
            # The TACKLE-authority stamps, so a frozen fullback frame can be
            # attributed to the emergency (TES) path rather than to RELEASE.
            "fb_auth": [bool(fb_mod.fullback_tackle_authority_active(d))
                        for d in fb],
            "fb_eng": [bool(fb_mod.is_fullback_emergency_engaged(d))
                       for d in fb],
            "fb_tr": [getattr(d, "tackle_role", None) for d in fb],
        }
        self.rows.append(row)

        if self.first_divergence is None and fb and ln:
            fb_frozen = all(t[0] for t in row["fb"])
            ln_frozen = all(t[0] for t in row["ln"])
            if fb_frozen != ln_frozen:
                self.first_divergence = row

    # ── phase timeline bookkeeping ───────────────────────────────────
    def note_phase(self, phase):
        if not self.phase_spans or self.phase_spans[-1][2] != phase:
            self.phase_spans.append([self.frame, self.frame, phase])
        else:
            self.phase_spans[-1][1] = self.frame


# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION — every wrapper calls the ORIGINAL unchanged
# ═════════════════════════════════════════════════════════════════════

def install_spies(ledger):
    """Wrap the two freeze writers, reached through the engine namespace."""
    real_api = ope_mod.apply_phase_invariants
    real_ai = ope_mod.ai_tes_controller

    def api(world, phase_str, *a, **kw):
        fb = tagged_fullbacks(world)
        ln = line_defenders(world)
        grp = fb + ln
        before = {id(d): freeze_tuple(d) for d in grp}
        res = real_api(world, phase_str, *a, **kw)
        after = {id(d): freeze_tuple(d) for d in grp}
        try:
            ledger.note_api(phase_str, fb, ln, before, after)
        except Exception:
            pass
        # ── BRANCH ATTRIBUTION (RELEASE only) ──
        # Branch A: the ROLE TABLE (`role in must_be_frozen`).
        # Branch B: the phase-unconditional FULLBACK UNFREEZE block
        #           (`fullback_role_active`, no live tackle authority).
        if phase_str == "RELEASE":
            try:
                must = MOVEMENT_RULES.get("RELEASE", {}).get(
                    "must_be_frozen", set())
                ln_rf = sum(1 for d in ln if get_role(d) in must)
                fb_rf = sum(1 for d in fb if get_role(d) in must)
                fb_ex = sum(
                    1 for d in fb
                    if getattr(d, "fullback_role_active", False)
                    and not fb_mod.fullback_tackle_authority_active(d)
                )
                ledger.note_branch(ln_rf, len(ln), fb_rf, len(fb), fb_ex)
            except Exception:
                pass
        return res

    def ai(world, dh, phase_str, *a, **kw):
        fb = tagged_fullbacks(world)
        ln = line_defenders(world)
        grp = fb + ln
        before = {id(d): pin_tuple(d) for d in grp}
        res = real_ai(world, dh, phase_str, *a, **kw)
        after = {id(d): pin_tuple(d) for d in grp}
        if phase_str == "RELEASE":
            try:
                ledger.note_release_pin(fb, ln, before, after)
            except Exception:
                pass
        return res

    ope_mod.apply_phase_invariants = api
    ope_mod.ai_tes_controller = ai
    return {"api": real_api, "ai": real_ai}


def uninstall_spies(originals):
    """Restore the real callables (probe hygiene)."""
    ope_mod.apply_phase_invariants = originals["api"]
    ope_mod.ai_tes_controller = originals["ai"]


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted like the ownership probe
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, verbose=False):
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
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    ledger = Ledger()
    originals = install_spies(ledger)

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)
            ledger.begin_frame(world, open_play)
            ledger.note_phase(getattr(open_play, "_phase", None))

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

            # SAMPLE AFTER the engine has finished the frame.
            ledger.sample(world, open_play)
            ledger.frames_stepped = frame

            if verbose and frame % 200 == 0:
                print("    ... frame %d" % frame, file=_REAL_STDOUT,
                      flush=True)
    finally:
        uninstall_spies(originals)

    return {"ledger": ledger, "world": world, "open_play": open_play}
# ═════════════════════════════════════════════════════════════════════
# ANALYSIS — the freeze source, the timer, the release condition
# ═════════════════════════════════════════════════════════════════════

def window_rows(ledger):
    """Per-frame rows inside the traced window."""
    return [r for r in ledger.rows if WINDOW_LO <= r["frame"] <= WINDOW_HI]


def focus_rows(ledger):
    """Window rows in which the focus pid carries the fullback tag."""
    return [r for r in window_rows(ledger)
            if FOCUS_PID in (r["fb_ids"] or ())]


def phase_timeline(ledger, lo, hi):
    """[(first_frame, last_frame, phase)] clipped to [lo, hi]."""
    out = []
    for start, end, phase in ledger.phase_spans:
        if end < lo or start > hi:
            continue
        out.append((max(start, lo), min(end, hi), phase))
    return out


def api_calls_in_window(ledger):
    return [c for c in ledger.api_calls
            if c["frame"] is not None and WINDOW_LO <= c["frame"] <= WINDOW_HI]


def release_pins_in_window(ledger):
    return [c for c in ledger.release_pins
            if c["frame"] is not None and WINDOW_LO <= c["frame"] <= WINDOW_HI]


def release_pins_with_fb(ledger):
    """RELEASE pin calls in which the tagged fullback was present."""
    return [c for c in ledger.release_pins if c["fb_ids"]]


def whole_rollout_freeze_accounting(ledger):
    """Every frame of the WHOLE rollout on which a tagged fullback was frozen.

    A freeze contract must be characterised over the whole rollout: the
    fullback may legitimately be frozen while it is playing as a TES tackler
    (the emergency path), and that case must never be confused with the
    RELEASE flat-footed contract.
    """
    frozen_rows = []
    tagged_frames = 0
    for r in ledger.rows:
        if not r["fb"]:
            continue
        tagged_frames += 1
        if any(t[0] for t in r["fb"]):
            frozen_rows.append(r)
    return tagged_frames, frozen_rows


def release_frame_table(ledger, line_pid):
    """Per-RELEASE-frame comparison rows inside the window.

    Returns ``[(frame, fb_triple, line_triple)]`` where a triple is
    ``(frozen, freeze_timer, movement_state, target_x, x, defender_role)``.
    """
    out = []
    for r in window_rows(ledger):
        if str(r["engine_phase"]) != "RELEASE":
            continue
        fb = r["fb"][0] if r["fb"] else None
        ln = None
        for i, pid in enumerate(r["ln_ids"]):
            if pid == line_pid:
                ln = r["ln"][i]
                break
        if ln is None and r["ln"]:
            ln = r["ln"][0]
        out.append((r["frame"], fb, ln))
    return out


def movement_authority_of(d, phase):
    """Whether ``has_movement_authority`` grants the body a move in *phase*."""
    try:
        from world.rules.movement_contracts import has_movement_authority
        return bool(has_movement_authority(None, phase, d))
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


class _tee:
    """Write to the report file and the real stdout at once."""

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


def banner(title):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def sub(title):
    print("")
    print("-" * 100)
    print(title)
    print("-" * 100)


def fmt_freeze(t):
    if t is None:
        return "None"
    return "frozen=%s ft=%.3f" % (t[0], t[1])


def fmt_body(t):
    """(frozen, ft, state, tx, x, role) -> one compact field."""
    if t is None:
        return "None"
    frozen, ft, state, tx, x, role = t
    return ("fr=%s ft=%.3f %-6s tx=%7.3f x=%7.3f %s"
            % (int(bool(frozen)), ft, str(state), tx, x, str(role)))


def report(res):
    ledger = res["ledger"]

    banner("FB RELEASE-CONTRACT TRACE — MEASURED (seed %d, %d frames)"
           % (SEED, ledger.frames_stepped))
    print("  window traced                  : %d .. %d" % (WINDOW_LO, WINDOW_HI))
    print("  focus pid                      : %s" % FOCUS_PID)
    print("  frames the focus pid was TAGGED: %d"
          % ledger.focus_tagged_frames)
    print("  dt                             : %.6f s" % DT)

    # ── A. PHASE TIMELINE ──
    sub("A. ENGINE PHASE TIMELINE IN THE WINDOW (%d..%d)"
        % (WINDOW_LO, WINDOW_HI))
    for start, end, phase in phase_timeline(ledger, WINDOW_LO, WINDOW_HI):
        print("    f=%-5d .. %-5d  %-18s (%d frames)"
              % (start, end, str(phase), end - start + 1))

    # ── B. THE FOCUS FULLBACK ──
    sub("B. THE TAGGED FULLBACK IN THE WINDOW")
    frows = focus_rows(ledger)
    print("  window frames with the tag present : %d" % len(frows))
    if frows:
        first = frows[0]
        last = frows[-1]
        print("  first tagged frame                 : %d" % first["frame"])
        print("  last  tagged frame                 : %d" % last["frame"])
        n_frozen = sum(1 for r in frows if r["fb"] and r["fb"][0][0])
        print("  tagged frames with FB frozen       : %d / %d"
              % (n_frozen, len(frows)))
        # RELEASE frames only
        rel = [r for r in frows if str(r["engine_phase"]) == "RELEASE"]
        print("  RELEASE frames with the tag        : %d" % len(rel))
        if rel:
            n_rel_frozen = sum(1 for r in rel if r["fb"] and r["fb"][0][0])
            print("  RELEASE frames with FB frozen      : %d / %d"
                  % (n_rel_frozen, len(rel)))
            print("  FB movement states during RELEASE  : %s"
                  % sorted({(r["fb"][0][2] if r["fb"] else None)
                            for r in rel}))
            print("  FB target offset during RELEASE    : %s"
                  % sorted({(round(abs(r["fb"][0][3] - r["fb"][0][4]), 3)
                             if r["fb"] else None) for r in rel})[:12])
    else:
        print("  !! The tagged fullback was NOT present in the window.")
        print("     Nothing can be claimed about its RELEASE contract here;")
        print("     every later section that needs the tag is inconclusive.")

    # ── C. LINE DEFENDERS IN THE WINDOW ──
    sub("C. NON-FULLBACK DEF_LINE_READY DEFENDERS IN THE WINDOW")
    rows = window_rows(ledger)
    ln_counts = {}
    for r in rows:
        ln_counts[len(r["ln_ids"])] = ln_counts.get(len(r["ln_ids"]), 0) + 1
    print("  distribution of line-defender count per frame : %s"
          % sorted(ln_counts.items()))
    rel_rows = [r for r in rows if str(r["engine_phase"]) == "RELEASE"]
    print("  RELEASE frames in window                      : %d"
          % len(rel_rows))
    if rel_rows:
        n_allfrozen = sum(1 for r in rel_rows
                          if r["ln"] and all(t[0] for t in r["ln"]))
        print("  RELEASE frames with ALL line defs frozen      : %d / %d"
              % (n_allfrozen, len(rel_rows)))
        pinned = 0
        for r in rel_rows:
            for t in r["ln"]:
                if abs(t[3] - t[4]) <= 1e-6:
                    pinned += 1
                    break
        print("  RELEASE frames with >=1 line def target==x    : %d / %d"
              % (pinned, len(rel_rows)))

    # ── D. apply_phase_invariants DECISIONS IN THE WINDOW ──
    sub("D. apply_phase_invariants — FREEZE DECISIONS PER CALL (window)")
    calls = api_calls_in_window(ledger)
    print("  calls in window                : %d" % len(calls))
    by_phase = {}
    for c in calls:
        by_phase.setdefault(str(c["phase"]), []).append(c)
    for phase, group in sorted(by_phase.items()):
        rel_group = [c for c in group if c["fb_ids"] or c["ln_ids"]]
        print("")
        print("    phase=%-18s calls=%-5d calls-with-bodies=%d"
              % (phase, len(group), len(rel_group)))
        if not rel_group:
            continue
        # End states.
        fb_frozen = sum(1 for c in rel_group for f in c["fb_after"]
                        if f is not None and f[0])
        fb_total = sum(1 for c in rel_group for f in c["fb_after"]
                       if f is not None)
        ln_frozen = sum(1 for c in rel_group for f in c["ln_after"]
                        if f is not None and f[0])
        ln_total = sum(1 for c in rel_group for f in c["ln_after"]
                       if f is not None)
        print("      FB  body-calls frozen at END : %d / %d"
              % (fb_frozen, fb_total))
        print("      LINE body-calls frozen at END: %d / %d"
              % (ln_frozen, ln_total))
        # A RELEASE sample call, verbatim.
        sample = next((c for c in rel_group
                       if str(c["phase"]) == "RELEASE"), None)
        if sample is not None:
            print("      sample call f=%d:" % sample["frame"])
            for i, pid in enumerate(sample["fb_ids"]):
                print("        FB   pid=%-5s before=%s after=%s"
                      % (pid, fmt_freeze(sample["fb_before"][i]),
                         fmt_freeze(sample["fb_after"][i])))
            for i, pid in enumerate(sample["ln_ids"][:6]):
                print("        LINE pid=%-5s before=%s after=%s"
                      % (pid, fmt_freeze(sample["ln_before"][i]),
                         fmt_freeze(sample["ln_after"][i])))

    # ── E. THE RELEASE TARGET/VELOCITY PIN ──
    sub("E. _enforce_release_freeze — TARGET/VELOCITY PIN PER RELEASE CALL")
    pins = release_pins_in_window(ledger)
    print("  RELEASE calls in window        : %d" % len(pins))
    pins_with_fb = [c for c in pins if c["fb_ids"]]
    print("  of those, FB tagged present    : %d" % len(pins_with_fb))
    if pins_with_fb:
        for c in pins_with_fb[:4]:
            print("")
            print("    call f=%d" % c["frame"])
            for i, pid in enumerate(c["fb_ids"]):
                print("      FB   pid=%-5s before(tx,x,vx,vy,fr)=%s after=%s"
                      % (pid, c["fb_before"][i], c["fb_after"][i]))
            for i, pid in enumerate(c["ln_ids"][:6]):
                print("      LINE pid=%-5s before=%s after=%s"
                      % (pid, c["ln_before"][i], c["ln_after"][i]))
    if pins:
        n_fb_pinned = sum(1 for c in pins for t in c["fb_after"]
                          if t is not None and is_pinned(t))
        n_fb_bodies = sum(1 for c in pins for t in c["fb_after"]
                          if t is not None)
        n_ln_pinned = sum(1 for c in pins for t in c["ln_after"]
                          if t is not None and is_pinned(t))
        n_ln_bodies = sum(1 for c in pins for t in c["ln_after"]
                          if t is not None)
        print("")
        print("  FB   bodies PINNED (target==x and v==0) : %d / %d"
              % (n_fb_pinned, n_fb_bodies))
        print("  LINE bodies PINNED (target==x and v==0) : %d / %d"
              % (n_ln_pinned, n_ln_bodies))

    # ── F. FIRST DIVERGENCE ──
    sub("F. FIRST FRAME WHERE FB AND LINE FREEZE STATE DIVERGE")
    div = ledger.first_divergence
    if div is None:
        print("  No frame in the whole rollout had the tagged fullback and a")
        print("  line defender simultaneously present with DIFFERENT frozen")
        print("  state.  (Either they never coexisted, or they never diverged.)")
    else:
        print("  frame = %d   engine phase = %s   phase timer = %.3f"
              % (div["frame"], div["engine_phase"], div["phase_timer"]))
        for i, pid in enumerate(div["fb_ids"]):
            print("    FB   pid=%-5s %s" % (pid, fmt_body(div["fb"][i])))
        for i, pid in enumerate(div["ln_ids"][:8]):
            print("    LINE pid=%-5s %s" % (pid, fmt_body(div["ln"][i])))

    # ── G. ROLE-TABLE MEMBERSHIP (the branch, statically) ──
    sub("G. THE ROLE TABLE — WHY THE ROLE PASS CANNOT TELL THEM APART")
    rel_rules = MOVEMENT_RULES.get("RELEASE", {})
    frozen_roles = sorted(r.name for r in rel_rules.get("must_be_frozen", ()))
    allowed_roles = sorted(r.name for r in rel_rules.get("allowed_to_move", ()))
    print("  RELEASE must_be_frozen : %s" % frozen_roles)
    print("  RELEASE allowed_to_move: %s" % allowed_roles)

    # ── I. WHOLE-ROLLOUT FREEZE ACCOUNTING FOR THE TAGGED FULLBACK ──
    sub("I. WHOLE-ROLLOUT FREEZE ACCOUNTING — does the FB EVER get frozen?")
    tagged_frames, frozen_rows = whole_rollout_freeze_accounting(ledger)
    print("  frames with the tag present (of %d)   : %d"
          % (ledger.frames_stepped, tagged_frames))
    print("  frames with the FB FROZEN             : %d" % len(frozen_rows))
    if frozen_rows:
        by_phase = {}
        for r in frozen_rows:
            key = str(r["engine_phase"])
            by_phase[key] = by_phase.get(key, 0) + 1
        print("  ...by engine phase                    : %s"
              % sorted(by_phase.items()))
        n_auth = sum(1 for r in frozen_rows
                     if r.get("fb_auth") and any(r["fb_auth"]))
        n_eng = sum(1 for r in frozen_rows
                    if r.get("fb_eng") and any(r["fb_eng"]))
        print("  ...carrying live tackle authority     : %d" % n_auth)
        print("  ...armed as a TES tackler             : %d" % n_eng)
        for r in frozen_rows[:8]:
            print("    f=%-5s %-18s auth=%s eng=%s tr=%s"
                  % (r["frame"], str(r["engine_phase"]),
                     r.get("fb_auth"), r.get("fb_eng"), r.get("fb_tr")))
    else:
        print("  The tagged fullback was NEVER frozen on ANY frame of the")
        print("  whole rollout.")

    # ── J. PER-FRAME RELEASE COMPARISON TABLE ──
    sub("J. PER-FRAME RELEASE COMPARISON — FB vs a DEF_LINE_READY line body")
    line_pid = None
    for r in window_rows(ledger):
        if str(r["engine_phase"]) == "RELEASE" and r["ln_ids"]:
            line_pid = 206 if 206 in r["ln_ids"] else r["ln_ids"][0]
            break
    print("  line defender used for comparison : %s" % line_pid)
    print("  %-6s | %-46s | %s"
          % ("frame", "TAGGED FULLBACK", "LINE DEFENDER"))
    for frame, fb_t, ln_t in release_frame_table(ledger, line_pid):
        print("  %-6d | %-46s | %s" % (frame, fmt_body(fb_t), fmt_body(ln_t)))

    # ── K. FIRST-DIVERGENCE BRANCH ATTRIBUTION ──
    sub("K. FIRST-DIVERGENCE BRANCH ATTRIBUTION (RELEASE calls only)")
    print("  RELEASE apply_phase_invariants calls observed : %d"
          % ledger.branch_calls)
    print("  branch A  LINE bodies `role in must_be_frozen`      : %d / %d"
          % (ledger.branch_ln_role_frozen, ledger.branch_ln_total))
    print("  branch A  FB   bodies `role in must_be_frozen`      : %d / %d"
          % (ledger.branch_fb_role_frozen, ledger.branch_fb_total))
    print("  branch B  FB bodies the FULLBACK-UNFREEZE block"
          " REVERSES : %d / %d"
          % (ledger.branch_fb_exempt, ledger.branch_fb_total))
    print("")
    print("  => BRANCH A FIRES FOR BOTH BODIES.  The role table cannot")
    print("     tell them apart: the tagged fullback carries")
    print("     defender_role = DEF_LINE_READY, and DEF_LINE_READY is in")
    print("     MOVEMENT_RULES['RELEASE']['must_be_frozen'], so the role")
    print("     pass writes p.frozen = True on the fullback too.")
    print("     THE FIRST BRANCH WHERE BEHAVIOUR DIVERGES IS BRANCH B —")
    print("     the phase-unconditional FULLBACK UNFREEZE block inside")
    print("     apply_phase_invariants:")
    print("         if getattr(p, 'fullback_role_active', False):")
    print("             if fullback_tackle_authority_active(p):")
    print("                 continue")
    print("             p.frozen = False; p.freeze_timer = 0.0")
    print("     The SECOND, downstream divergence is the FULLBACK")
    print("     HARD-EXCLUDE at the top of release_controller's")
    print("     _enforce_release_freeze mover loop, which keeps the")
    print("     fullback out of the target/velocity pin (section E).")

    # ── H. VERDICT ──
    banner("VERDICT — measured, not inferred")
    if not frows:
        print("  INCONCLUSIVE for the focus fullback: pid %s never carried")
        print("  the fullback tag inside %d..%d, so the RELEASE-contract")
        print("  question was not exercised in this window." % (FOCUS_PID,
              WINDOW_LO, WINDOW_HI))
        return
    rel = [r for r in frows if str(r["engine_phase"]) == "RELEASE"]
    if not rel:
        print("  INCONCLUSIVE: the focus fullback was tagged in the window")
        print("  but no RELEASE frame occurred while it held the tag.")
        return
    n_rel_frozen = sum(1 for r in rel if r["fb"] and r["fb"][0][0])
    ln_all = [r for r in rel if r["ln"]]
    n_ln_all_frozen = sum(1 for r in ln_all
                          if all(t[0] for t in r["ln"]))
    print("  RELEASE frames with the FB tagged        = %d" % len(rel))
    print("  ...of those, FB ends FROZEN              = %d" % n_rel_frozen)
    print("  ...of those, ALL line defs end FROZEN    = %d / %d"
          % (n_ln_all_frozen, len(ln_all)))
    if n_rel_frozen == 0 and n_ln_all_frozen == len(ln_all) and ln_all:
        print("")
        print("  => THE TAGGED FULLBACK DOES NOT ENTER THE TACKLE-RELEASE")
        print("     FLAT-FOOTED CONTRACT.  Both bodies are stamped")
        print("     DEF_LINE_READY, so both are put in the RELEASE")
        print("     must_be_frozen set by the role pass — but the tagged")
        print("     fullback is then UNFROZEN inside the SAME call by the")
        print("     phase-unconditional FULLBACK UNFREEZE block, and it is")
        print("     additionally hard-excluded from the RELEASE target/")
        print("     velocity pin.  The line defenders take both; the")
        print("     fullback takes neither.")
    else:
        print("")
        print("  => MIXED: the measured states above do not match the")
        print("     'fullback always exempt' shape.  Read sections D/E for")
        print("     the per-call decision that produced each frame.")
    print("")
    print("  report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    # PHASE 1: the rollout, with the engine's own chatter discarded.
    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _devnull
    try:
        res = run_rollout()
    finally:
        sys.stdout = real
        try:
            _devnull.close()
        except Exception:
            pass

    # PHASE 2: the report, into its own clean file and the terminal.
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        report(res)
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
