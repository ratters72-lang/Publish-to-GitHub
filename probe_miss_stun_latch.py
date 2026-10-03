"""probe_miss_stun_latch.py -- measure the miss-stun hold.  Nothing inferred.

THE QUESTION
------------
``pretackle_machine.apply_outcome`` writes ``freeze_timer`` (0.2 s / 0.5 s,
``max()`` semantics -- verified in source) on a missed tackle.  No module read
so far decrements ``freeze_timer``; it is only set, and zeroed at seven lifecycle
boundaries -- the decisive one for a miss being ``_clear_tes_artefacts`` at the
NEXT tackle's RELEASE.  This probe MEASURES that, and measures three things the
audit could not settle from source alone:

  U4  the observed hold duration, per set-event, in frames;
  U8  which writer owns the target on a latched frame (the ``target_source``
      histogram), i.e. whether step 7's pin, the corridor, or
      ``apply_pre_tackle`` Pass 0 wins;
  U9  whether ``intercept_tacklers.apply_pre_tackle_stance`` fires on a
      latched body (``pre_tackle_stance`` / ``footplanting`` / ``move_speed``).

It also measures the two things the success criteria in the audit need as a
BEFORE baseline:

  * the clear-cause histogram (which boundary releases a stunned body), and
  * the zero-speed-with-live-offset-target body-frame count, using the engine's
    OWN predicate (``FreezeAndMovementAuditAgent._is_soft_frozen``, verified in
    ``game/debug/freeze_audit.py``) rather than a paraphrase of it.

METHOD — sampling, not inference
--------------------------------
Every spy calls the real callable UNCHANGED.  The per-frame ledger is built by
SAMPLING each player's ``freeze_timer`` after the engine has finished the frame
and differencing against the previous frame.  A set is ``prev <= 0 < cur``; a
clear is ``prev > 0 and cur <= 0``.  Intra-frame set-then-clear is invisible to
the sampler, so the direct ``apply_outcome`` call record and the boundary-call
flags are kept alongside it and cross-checked; the report states which evidence
each number came from.

Read-only: no engine module is written by this file, and the only patched
attributes are probe-local wrappers around the originals.

Run:  python probe_miss_stun_latch.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's own per-frame physics chatter would drown the probe report.
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

# The stun writer under measurement.
from game.defence.defence_lvl_1 import pretackle_machine as pm_mod

# The engine's OWN soft-freeze predicate -- reused, not re-implemented.
from game.debug.freeze_audit import FreezeAndMovementAuditAgent


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
# Frame count is env-overridable so an A/B can run SHORT and write two
# separate reports without editing this file.
MAX_FRAMES = int(os.environ.get("RLM_MISS_PROBE_FRAMES", "1200"))
SEED = int(os.environ.get("RLM_MISS_PROBE_SEED", "20260827"))
DT = 1.0 / 60.0

# Float tolerance for "timer is running".
TIMER_EPS = 1e-9

# The engine's soft-freeze epsilons, mirrored for the ledger's own columns
# (the authoritative predicate used for the COUNT is the engine's).
OFFSET_EPS_M = 0.05
SPEED_EPS_MPS = 0.01

# Detail-row caps so a long rollout cannot exhaust memory.
MAX_SET_ROWS = 4000
MAX_LATCH_ROWS = 4000

REPORT_PATH = os.environ.get(
    "RLM_MISS_PROBE_REPORT", "tmp_miss_stun_latch_report.txt"
)

# The clear sites named in the audit (V6), as spy keys.
BOUNDARY_KEYS = (
    "clear_tes_artefacts",
    "post_ruck_marker_cleanup",
    "reset_tackle_state",
    "assign_markers_for_new_tackle",
)


def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fnum(v):
    """Return *v* as float when numeric, else None (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pid_of(p):
    return getattr(p, "player_id", None)


def all_players(world):
    """Every distinct player on the world (attacking + defending + BC)."""
    seen = set()
    out = []
    for group in ("attacking_team", "defending_team"):
        for p in getattr(world, group, None) or []:
            if p is None or id(p) in seen:
                continue
            seen.add(id(p))
            out.append(p)
    bc = getattr(world, "ball_carrier", None)
    if bc is not None and id(bc) not in seen:
        seen.add(id(bc))
        out.append(bc)
    # Some worlds expose only ``players``.
    for p in getattr(world, "players", None) or []:
        if p is None or id(p) in seen:
            continue
        seen.add(id(p))
        out.append(p)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SPIES
# ═════════════════════════════════════════════════════════════════════

class Ledger:
    """Accumulator for every measured fact in the rollout."""

    def __init__(self):
        self.frame = 0
        # per-frame boundary flags (which clear-capable site ran this frame)
        self.frame_boundaries = set()
        self.frame_broken_tackle = False
        self.frame_dispatch = False
        self.frame_phase = None
        self.frame_sub_state = None
        self.frame_engine_phase = None
        # direct records
        self.outcome_calls = []        # every apply_outcome invocation
        self.broken_tackle_calls = []  # every _handle_broken_tackle invocation
        self.marker_assign_calls = []  # every assign_markers_for_new_tackle
        # sampled transitions
        self.set_events = []           # timer went <=0 -> >0
        self.clear_events = []         # timer went >0  -> <=0
        self.latched_rows = []         # one row per latched body-frame
        self.soft_frozen_rows = []     # one row per soft-frozen body-frame
        self.frames_stepped = 0
        self.open_play_frames = 0
        self.dispatch_frames = 0
        # raw per-frame body ledger is kept only as the previous-frame dict
        self._prev_timer = {}
        self._prev_x = {}
        self._open_since = {}          # pid -> frame the current set began
        self._latch_streak = {}        # pid -> consecutive latched frames

    def begin_frame(self, world, open_play):
        self.frame = int(getattr(world, "frame_count", 0) or 0)
        self.frame_boundaries = set()
        self.frame_broken_tackle = False
        self.frame_dispatch = False
        self.frame_phase = name_of(getattr(world, "phase", None))
        self.frame_sub_state = name_of(getattr(world, "sub_state", None))
        self.frame_engine_phase = name_of(getattr(open_play, "_phase", None))

    def note_boundary(self, key):
        self.frame_boundaries.add(key)

    # ── direct call records ─────────────────────────────────────────

    def note_outcome(self, world, defender, bc, outcome, state, before, after):
        """Record one apply_outcome invocation with its full surroundings."""
        vx = fnum(getattr(defender, "vx", 0.0)) or 0.0
        vy = fnum(getattr(defender, "vy", 0.0)) or 0.0
        x = fnum(getattr(defender, "x", 0.0)) or 0.0
        y = fnum(getattr(defender, "y", 0.0)) or 0.0
        tx = fnum(getattr(defender, "target_x", None))
        ty = fnum(getattr(defender, "target_y", None))
        off_x = None if tx is None else abs(tx - x)
        off_y = None if ty is None else abs(ty - y)
        row = {
            "frame": self.frame,
            "phase": self.frame_phase,
            "sub_state": self.frame_sub_state,
            "engine_phase": self.frame_engine_phase,
            "pid": pid_of(defender),
            "machine_state": name_of(state),
            "outcome": name_of(outcome),
            "timer_before": before,
            "timer_after": after,
            "rearmed": bool(before is not None and before > TIMER_EPS),
            "vx": round(vx, 4),
            "vy": round(vy, 4),
            "speed": round(math.hypot(vx, vy), 4),
            "off_x": None if off_x is None else round(off_x, 3),
            "off_y": None if off_y is None else round(off_y, 3),
            "move_speed": fnum(getattr(defender, "move_speed", None)),
            "target_source": getattr(defender, "target_source", None),
            "movement_state": name_of(getattr(defender, "movement_state", None)),
            "pre_tackle_state": name_of(getattr(defender, "pre_tackle_state", None)),
            "tackle_role": getattr(defender, "tackle_role", None),
            "dist_bc": (
                None if bc is None else round(
                    math.hypot(x - (fnum(getattr(bc, "x", 0.0)) or 0.0),
                               y - (fnum(getattr(bc, "y", 0.0)) or 0.0)), 3,
                )
            ),
        }
        # "zero velocity with a live offset target, immediately after the miss"
        row["miss_leaves_offset_target"] = bool(
            row["speed"] < SPEED_EPS_MPS
            and ((off_x is not None and off_x > OFFSET_EPS_M)
                 or (off_y is not None and off_y > OFFSET_EPS_M))
        )
        self.outcome_calls.append(row)

    def note_broken_tackle(self):
        self.frame_broken_tackle = True
        self.broken_tackle_calls.append({
            "frame": self.frame,
            "phase": self.frame_phase,
            "sub_state": self.frame_sub_state,
        })

    def note_marker_assign(self):
        self.marker_assign_calls.append({
            "frame": self.frame,
            "phase": self.frame_phase,
            "sub_state": self.frame_sub_state,
        })
        self.note_boundary("assign_markers_for_new_tackle")

    # ── per-frame sampling ──────────────────────────────────────────

    def sample(self, world, audit):
        """Difference freeze_timer for every player; build the body ledger."""
        players = all_players(world)
        cur_timer = {}
        for p in players:
            pid = pid_of(p)
            if pid is None:
                continue
            cur = fnum(getattr(p, "freeze_timer", 0.0)) or 0.0
            prev = self._prev_timer.get(pid)
            cur_timer[pid] = cur

            # ── SET (<=0 -> >0) ──
            if prev is not None and prev <= TIMER_EPS and cur > TIMER_EPS:
                self._open_since[pid] = self.frame
                src = "unknown"
                for row in self.outcome_calls:
                    if (row["frame"] == self.frame and row["pid"] == pid
                            and row["outcome"] in ("FALL_OVER", "STUNNED_UPRIGHT")):
                        src = "apply_outcome:" + str(row["outcome"])
                        break
                else:
                    if self.frame_broken_tackle:
                        src = "_handle_broken_tackle"
                    elif "assign_markers_for_new_tackle" in self.frame_boundaries:
                        src = "assign_markers_for_new_tackle"
                self.set_events.append({
                    "frame": self.frame,
                    "pid": pid,
                    "value": round(cur, 4),
                    "source": src,
                    "phase": self.frame_phase,
                    "sub_state": self.frame_sub_state,
                    "engine_phase": self.frame_engine_phase,
                })

            # ── CLEAR (>0 -> <=0) ──
            if prev is not None and prev > TIMER_EPS and cur <= TIMER_EPS:
                started = self._open_since.pop(pid, None)
                self.clear_events.append({
                    "frame": self.frame,
                    "pid": pid,
                    "value_before": round(prev, 4),
                    "started_frame": started,
                    "held_frames": (
                        None if started is None else self.frame - started
                    ),
                    "boundaries": sorted(self.frame_boundaries),
                    "broken_tackle": self.frame_broken_tackle,
                    "phase": self.frame_phase,
                    "sub_state": self.frame_sub_state,
                    "engine_phase": self.frame_engine_phase,
                })

            # ── LATCHED BODY-FRAME ──
            if cur > TIMER_EPS:
                self._latch_streak[pid] = self._latch_streak.get(pid, 0) + 1
                if len(self.latched_rows) < MAX_LATCH_ROWS:
                    x = fnum(getattr(p, "x", 0.0)) or 0.0
                    y = fnum(getattr(p, "y", 0.0)) or 0.0
                    tx = fnum(getattr(p, "target_x", None))
                    ty = fnum(getattr(p, "target_y", None))
                    self.latched_rows.append({
                        "frame": self.frame,
                        "pid": pid,
                        "phase": self.frame_phase,
                        "sub_state": self.frame_sub_state,
                        "engine_phase": self.frame_engine_phase,
                        "timer": round(cur, 4),
                        "latch_streak": self._latch_streak[pid],
                        "target_source": getattr(p, "target_source", None),
                        "movement_state": name_of(
                            getattr(p, "movement_state", None)),
                        "frozen": bool(getattr(p, "frozen", False)),
                        "frozen_after_frame": bool(getattr(p, "frozen", False)),
                        "move_speed": fnum(getattr(p, "move_speed", None)),
                        "pre_tackle_stance": bool(
                            getattr(p, "pre_tackle_stance", False)),
                        "footplanting": bool(getattr(p, "footplanting", False)),
                        "tackle_role": getattr(p, "tackle_role", None),
                        "pre_tackle_state": name_of(
                            getattr(p, "pre_tackle_state", None)),
                        "mmdl_moved": bool(getattr(p, "mmdl_moved", False)
                                           if hasattr(p, "mmdl_moved")
                                           else getattr(p, "_mmdl_moved", False)),
                        "corridor": bool(getattr(p, "_pta_corridor", False)),
                        "speed": round(math.hypot(
                            fnum(getattr(p, "vx", 0.0)) or 0.0,
                            fnum(getattr(p, "vy", 0.0)) or 0.0), 4),
                        "off_x": None if tx is None else round(abs(tx - x), 3),
                        "off_y": None if ty is None else round(abs(ty - y), 3),
                    })
            else:
                self._latch_streak[pid] = 0

            # ── SOFT-FROZEN BODY-FRAME (the engine's own predicate) ──
            try:
                if audit._is_soft_frozen(p):
                    x = fnum(getattr(p, "x", 0.0)) or 0.0
                    y = fnum(getattr(p, "y", 0.0)) or 0.0
                    tx = fnum(getattr(p, "target_x", None))
                    ty = fnum(getattr(p, "target_y", None))
                    self.soft_frozen_rows.append({
                        "frame": self.frame,
                        "pid": pid,
                        "phase": self.frame_phase,
                        "sub_state": self.frame_sub_state,
                        "timer": round(cur, 4),
                        "frozen": bool(getattr(p, "frozen", False)),
                        "target_source": getattr(p, "target_source", None),
                        "movement_state": name_of(
                            getattr(p, "movement_state", None)),
                        "tackle_role": getattr(p, "tackle_role", None),
                        "pre_tackle_state": name_of(
                            getattr(p, "pre_tackle_state", None)),
                        "move_speed": fnum(getattr(p, "move_speed", None)),
                        "off_x": None if tx is None else round(abs(tx - x), 3),
                        "off_y": None if ty is None else round(abs(ty - y), 3),
                    })
            except Exception:
                pass

        if self.frame_dispatch:
            self.dispatch_frames += 1
        self._prev_timer = cur_timer


# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION
# ═════════════════════════════════════════════════════════════════════

def install_spies(open_play, ledger):
    """Wrap the real entry points; every wrapper calls the original."""
    real_outcome = pm_mod.apply_outcome
    real_clear_tes = open_play._clear_tes_artefacts
    real_post_ruck = open_play._post_ruck_marker_cleanup
    real_reset_tackle = open_play._reset_tackle_state
    real_marker = open_play.defence_manager.assign_markers_for_new_tackle
    real_broken = open_play._handle_broken_tackle
    real_dispatch = open_play.defence_manager.update_open_play

    def outcome(world, defender, bc, tuners, state=None, *a, **kw):
        before = fnum(getattr(defender, "freeze_timer", 0.0))
        res = real_outcome(world, defender, bc, tuners, state=state, *a, **kw)
        after = fnum(getattr(defender, "freeze_timer", 0.0))
        try:
            ledger.note_outcome(world, defender, bc, res, state, before, after)
        except Exception:
            pass
        return res

    def clear_tes(*a, **kw):
        ledger.note_boundary("clear_tes_artefacts")
        return real_clear_tes(*a, **kw)

    def post_ruck(*a, **kw):
        ledger.note_boundary("post_ruck_marker_cleanup")
        return real_post_ruck(*a, **kw)

    def reset_tackle(*a, **kw):
        ledger.note_boundary("reset_tackle_state")
        return real_reset_tackle(*a, **kw)

    # NOTE: every engine site below is a BOUND METHOD, so the caller passes
    # its arguments WITHOUT ``self`` (e.g. ``self._handle_broken_tackle(
    # bump_off=True)``).  The wrappers therefore take pure varargs.  A wrapper
    # that demanded a positional ``world`` broke the broken-tackle call on the
    # first live bump-off (TypeError: missing 1 required positional argument).
    def marker(*a, **kw):
        ledger.note_marker_assign()
        return real_marker(*a, **kw)

    def broken(*a, **kw):
        ledger.note_broken_tackle()
        return real_broken(*a, **kw)

    def dispatch(*a, **kw):
        ledger.frame_dispatch = True
        return real_dispatch(*a, **kw)

    pm_mod.apply_outcome = outcome
    open_play._clear_tes_artefacts = clear_tes
    open_play._post_ruck_marker_cleanup = post_ruck
    open_play._reset_tackle_state = reset_tackle
    open_play.defence_manager.assign_markers_for_new_tackle = marker
    open_play._handle_broken_tackle = broken
    open_play.defence_manager.update_open_play = dispatch


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted as probe_open_play_stall_authority
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
    audit = FreezeAndMovementAuditAgent()
    install_spies(open_play, ledger)

    for frame in range(1, frames + 1):
        world.frame_count = frame
        ball.update(DT)

        ledger.begin_frame(world, open_play)
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
                collision_system.resolve_all(world.players, world=world)

        # SAMPLE AFTER the engine has finished the frame.
        ledger.sample(world, audit)
        ledger.frames_stepped = frame
        if ledger.frame_engine_phase == "OPEN_PLAY":
            ledger.open_play_frames += 1

        # Heartbeat onto the REAL stdout (the rollout's own stdout is the
        # null device), so progress is visible without flooding the log.
        if frame % 100 == 0:
            print("    ... frame %d" % frame, file=_REAL_STDOUT, flush=True)

    return {
        "ledger": ledger,
        "world": world,
        "open_play": open_play,
    }


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═════════════════════════════════════════════════════════════════════

def histogram(rows, key_fn, limit=20):
    hist = {}
    for r in rows:
        k = key_fn(r)
        hist[k] = hist.get(k, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def duration_stats(clear_events):
    """Hold durations in frames, overall and per set-source."""
    by_src = {}
    all_vals = []
    for c in clear_events:
        held = c.get("held_frames")
        if held is None or held <= 0:
            continue
        all_vals.append(held)
        src = c.get("set_source") or "<unknown>"
        by_src.setdefault(src, []).append(held)
    return all_vals, by_src


def longest_latch_runs(latched_rows):
    """Longest consecutive latched-frame run per player."""
    per_pid = {}
    for r in latched_rows:
        per_pid.setdefault(r["pid"], []).append(r["frame"])
    runs = {}
    for pid, frames in per_pid.items():
        frames = sorted(frames)
        best = cur = 1
        for i in range(1, len(frames)):
            cur = cur + 1 if frames[i] == frames[i - 1] + 1 else 1
            best = max(best, cur)
        runs[pid] = {"n": len(frames), "longest": best,
                     "first": frames[0], "last": frames[-1]}
    return runs


def rearm_stats(outcome_calls, stun_outcomes=("FALL_OVER", "STUNNED_UPRIGHT")):
    """Re-arm evidence: stun applied while the timer was already running."""
    stun_calls = [r for r in outcome_calls if r["outcome"] in stun_outcomes]
    rearmed = [r for r in stun_calls if r["rearmed"]]
    # longest run of consecutive frames applying a stun to the same body
    per_pid = {}
    for r in stun_calls:
        per_pid.setdefault(r["pid"], []).append(r)
    runs = {}
    for pid, rows in per_pid.items():
        rows = sorted(rows, key=lambda r: r["frame"])
        best = cur = 1
        for i in range(1, len(rows)):
            cur = cur + 1 if rows[i]["frame"] == rows[i - 1]["frame"] + 1 else 1
            best = max(best, cur)
        runs[pid] = {"calls": len(rows), "longest_consecutive": best}
    return stun_calls, rearmed, runs


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


class tee:
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


def print_rows(title, rows, n=12):
    print("  -- %s --" % title)
    for k, v in rows[:n]:
        print("    %-42s = %d" % (str(k), v))


def report(res):
    led = res["ledger"]
    banner("MISS-STUN LATCH -- MEASURED")
    print("  seed                          : %d" % SEED)
    print("  frames stepped                : %d" % led.frames_stepped)
    print("  OPEN_PLAY frames (engine own) : %d" % led.open_play_frames)
    print("  dt                            : %.6f s (%.1f Hz)"
          % (DT, 1.0 / DT))

    # ── COVERAGE ──
    sub("A. COVERAGE -- did the instrument actually observe the miss path?")
    print("  apply_outcome invocations     : %d" % len(led.outcome_calls))
    print("  _handle_broken_tackle calls   : %d" % len(led.broken_tackle_calls))
    print("  assign_markers calls          : %d" % len(led.marker_assign_calls))
    print("  frames defence dispatch ran    : %d" % led.dispatch_frames)
    if led.outcome_calls:
        print_rows("apply_outcome by outcome",
                   histogram(led.outcome_calls, lambda r: r["outcome"], 10))
        print_rows("apply_outcome by machine state",
                   histogram(led.outcome_calls, lambda r: r["machine_state"], 14))
        print_rows("apply_outcome by phase",
                   histogram(led.outcome_calls, lambda r: r["phase"], 8))
    else:
        print("  !! ZERO apply_outcome calls captured.  Either the miss path")
        print("     never fired in this rollout, or the patch did not take.")
        print("     Treat every subsequent section as INCONCLUSIVE.")

    # ── SET / CLEAR TRANSITIONS ──
    sub("B. SET EVENTS -- who started the timer")
    print("  sampled set events            : %d" % len(led.set_events))
    print_rows("set source",
               histogram(led.set_events, lambda r: r["source"], 12))
    print_rows("set by outcome frame phase",
               histogram(led.set_events, lambda r: r["phase"], 10))
    vals = [r["value"] for r in led.set_events]
    if vals:
        print("  set values (s): n=%d min=%.3f max=%.3f distinct=%s"
              % (len(vals), min(vals), max(vals),
                 sorted(set(round(v, 3) for v in vals))[:12]))

    sub("C. CLEAR EVENTS -- who released the timer, and how long it HELD")
    print("  sampled clear events          : %d" % len(led.clear_events))
    per_boundary = {}
    per_boundaryset = {}
    for c in led.clear_events:
        bs = c.get("boundaries") or []
        if not bs:
            key = "NONE_OBSERVED"
        else:
            key = "+".join(bs)
        per_boundaryset[key] = per_boundaryset.get(key, 0) + 1
        for b in bs:
            per_boundary[b] = per_boundary.get(b, 0) + 1
    print("  -- clear attributed to the boundary set that fired that frame --")
    for k, v in sorted(per_boundaryset.items(), key=lambda kv: -kv[1])[:20]:
        print("    %-46s = %d" % (k, v))
    print("  -- per boundary (a frame may fire several) --")
    for k, v in sorted(per_boundary.items(), key=lambda kv: -kv[1]):
        print("    %-46s = %d" % (k, v))

    held = [c["held_frames"] for c in led.clear_events
            if c.get("held_frames") is not None and c["held_frames"] > 0]
    if held:
        held_sorted = sorted(held)
        print("")
        print("  hold duration (frames): n=%d  min=%d  median=%d  p90=%d  max=%d"
              % (len(held), held_sorted[0],
                 held_sorted[len(held_sorted) // 2],
                 held_sorted[int(len(held_sorted) * 0.9)],
                 held_sorted[-1]))
        print("  hold duration (seconds @ %.4f s/frame): min=%.2f median=%.2f "
              "p90=%.2f max=%.2f"
              % (DT, held_sorted[0] * DT,
                 held_sorted[len(held_sorted) // 2] * DT,
                 held_sorted[int(len(held_sorted) * 0.9)] * DT,
                 held_sorted[-1] * DT))
        bucket = {}
        for v in held:
            b = "1-5" if v <= 5 else ("6-15" if v <= 15 else
                                      ("16-30" if v <= 30 else
                                       ("31-60" if v <= 60 else
                                        ("61-180" if v <= 180 else ">180"))))
            bucket[b] = bucket.get(b, 0) + 1
        print("  -- hold duration buckets (frames) --")
        for k in ("1-5", "6-15", "16-30", "31-60", "61-180", ">180"):
            if k in bucket:
                print("    %-12s = %d" % (k, bucket[k]))
        print("")
        print("  CONFIGURED stun is 0.5 s (FALL_OVER) / 0.2 s (STUNNED_UPRIGHT)")
        print("  => %.0f frames / %.0f frames at %.1f Hz."
              % (0.5 / DT, 0.2 / DT, 1.0 / DT))
    else:
        print("  (no complete set->clear pair was observed)")

    still_open = sorted(led._open_since.items())
    if still_open:
        print("")
        print("  sets that NEVER cleared inside the rollout: %d" % len(still_open))
        for pid, f0 in still_open[:12]:
            print("    pid=%-5s set at frame %-6d (never released)" % (pid, f0))

    # ── U8 / U9: THE LATCHED FRAME ──
    sub("D. THE LATCHED BODY-FRAME (U8/U9) -- who owns the target while held")
    print("  latched body-frames sampled   : %d" % len(led.latched_rows))
    if led.latched_rows:
        print_rows("target_source while latched",
                   histogram(led.latched_rows, lambda r: r["target_source"], 20))
        print_rows("movement_state while latched",
                   histogram(led.latched_rows, lambda r: r["movement_state"], 12))
        print_rows("engine phase while latched",
                   histogram(led.latched_rows, lambda r: r["engine_phase"], 10))
        print_rows("tackle_role while latched",
                   histogram(led.latched_rows, lambda r: r["tackle_role"], 10))
        print_rows("defender_role/pre_tackle_state while latched",
                   histogram(led.latched_rows, lambda r: r["pre_tackle_state"], 10))
        n_frozen = sum(1 for r in led.latched_rows if r["frozen"])
        n_stance = sum(1 for r in led.latched_rows if r["pre_tackle_stance"])
        n_foot = sum(1 for r in led.latched_rows if r["footplanting"])
        n_corr = sum(1 for r in led.latched_rows if r["corridor"])
        n_mm = sum(1 for r in led.latched_rows if r["mmdl_moved"])
        n_ms0 = sum(1 for r in led.latched_rows
                    if (r["move_speed"] is not None and r["move_speed"] <= 0.0))
        n_pinned = sum(
            1 for r in led.latched_rows
            if (r["off_x"] is not None and r["off_x"] <= OFFSET_EPS_M
                and (r["off_y"] is None or r["off_y"] <= OFFSET_EPS_M))
        )
        n_offset = sum(
            1 for r in led.latched_rows
            if ((r["off_x"] is not None and r["off_x"] > OFFSET_EPS_M)
                or (r["off_y"] is not None and r["off_y"] > OFFSET_EPS_M))
        )
        print("")
        print("  frozen==True while latched          = %d / %d" % (n_frozen, len(led.latched_rows)))
        print("  target == position (pinned)          = %d / %d" % (n_pinned, len(led.latched_rows)))
        print("  target OFF position (offset target)  = %d / %d" % (n_offset, len(led.latched_rows)))
        print("  move_speed <= 0 while latched        = %d / %d" % (n_ms0, len(led.latched_rows)))
        print("  pre_tackle_stance True (U9)          = %d / %d" % (n_stance, len(led.latched_rows)))
        print("  footplanting True (U9)               = %d / %d" % (n_foot, len(led.latched_rows)))
        print("  corridor latch set                   = %d / %d" % (n_corr, len(led.latched_rows)))
        print("  _mmdl_moved True                     = %d / %d" % (n_mm, len(led.latched_rows)))

        runs = longest_latch_runs(led.latched_rows)
        print("")
        print("  longest consecutive LATCHED run per player:")
        print("  %-6s %-8s %-12s %-8s %-8s" %
              ("pid", "frames", "longest_run", "first", "last"))
        for pid, v in sorted(runs.items(), key=lambda kv: -kv[1]["longest"]):
            print("  %-6s %-8s %-12s %-8s %-8s"
                  % (pid, v["n"], v["longest"], v["first"], v["last"]))
        print("")
        print("  players latched >= 30 consecutive frames = %d %s"
              % (len([1 for v in runs.values() if v["longest"] >= 30]),
                 sorted([p for p, v in runs.items() if v["longest"] >= 30])))

        # one representative row per (source, state)
        print("")
        print("  REPRESENTATIVE LATCHED ROWS (one per target_source / state):")
        groups = {}
        for r in led.latched_rows:
            groups.setdefault((r["target_source"], r["movement_state"]), []).append(r)
        for key in sorted(groups, key=lambda k: -len(groups[k]))[:10]:
            r = groups[key][0]
            print("    --- src=%s state=%s : %d row(s)"
                  % (key[0], key[1], len(groups[key])))
            print("        f=%s pid=%s timer=%.3f streak=%s frozen=%s "
                  "move_speed=%s stance=%s foot=%s mmdl=%s corr=%s "
                  "off_x=%s off_y=%s"
                  % (r["frame"], r["pid"], r["timer"], r["latch_streak"],
                     r["frozen"], r["move_speed"], r["pre_tackle_stance"],
                     r["footplanting"], r["mmdl_moved"], r["corridor"],
                     r["off_x"], r["off_y"]))

    # ── THE OFFSET-TARGET INVARIANT (criterion 3 baseline) ──
    sub("E. ZERO-SPEED WITH LIVE OFFSET TARGET -- the engine's own predicate")
    print("  soft-frozen body-frames (engine predicate) : %d"
          % len(led.soft_frozen_rows))
    if led.soft_frozen_rows:
        print_rows("soft-frozen by target_source",
                   histogram(led.soft_frozen_rows, lambda r: r["target_source"], 20))
        print_rows("soft-frozen by movement_state",
                   histogram(led.soft_frozen_rows, lambda r: r["movement_state"], 12))
        print_rows("soft-frozen by engine phase",
                   histogram(led.soft_frozen_rows, lambda r: r["engine_phase"], 10))
        print_rows("soft-frozen by tackle_role",
                   histogram(led.soft_frozen_rows, lambda r: r["tackle_role"], 12))
        n_frozen = sum(1 for r in led.soft_frozen_rows if r["frozen"])
        n_timer = sum(1 for r in led.soft_frozen_rows if (r["timer"] or 0) > 0)
        print("  of those, frozen==True                     : %d" % n_frozen)
        print("  of those, freeze_timer>0                    : %d" % n_timer)
        print("  of those, frozen==False (the reported shape): %d"
              % (len(led.soft_frozen_rows) - n_frozen))
        the_shape = [r for r in led.soft_frozen_rows if not r["frozen"]]
        if the_shape:
            print_rows("frozen==False: by target_source",
                       histogram(the_shape, lambda r: r["target_source"], 20))

    # ── THE MISS ITSELF: does the miss leave an offset target? ──
    sub("F. THE MISS ITSELF -- what apply_outcome leaves behind")
    if led.outcome_calls:
        print_rows("outcome",
                   histogram(led.outcome_calls, lambda r: r["outcome"], 8))
        for oc in ("BOUNCE_BACK", "FALL_OVER", "STUNNED_UPRIGHT", "WRONG_FOOT"):
            rows = [r for r in led.outcome_calls if r["outcome"] == oc]
            if not rows:
                continue
            n_off = sum(1 for r in rows if r["miss_leaves_offset_target"])
            print("  %-18s n=%-5d zero-speed+offset-target immediately after = %d"
                  % (oc, len(rows), n_off))
        print("")
        print_rows("target_source at the miss",
                   histogram(led.outcome_calls, lambda r: r["target_source"], 16))
        print_rows("move_speed at the miss",
                   histogram(led.outcome_calls, lambda r: r["move_speed"], 10))
        print_rows("distance to BC at the miss (m)",
                   histogram(led.outcome_calls,
                             lambda r: (None if r["dist_bc"] is None
                                        else round(r["dist_bc"])), 10))

    # ── RE-ARM ──
    sub("G. RE-ARM -- is the stun re-applied while it is already running?")
    stun_calls, rearmed, runs = rearm_stats(led.outcome_calls)
    print("  stun outcomes (FALL_OVER/STUNNED_UPRIGHT) applied : %d"
          % len(stun_calls))
    print("  of those, timer ALREADY running at call time      : %d"
          % len(rearmed))
    if stun_calls:
        print("  re-arm rate                                       : %.1f%%"
              % (100.0 * len(rearmed) / len(stun_calls)))
    if runs:
        print("")
        print("  consecutive-frame stun applications per player:")
        print("  %-6s %-10s %-20s" % ("pid", "calls", "longest_consecutive"))
        for pid, v in sorted(runs.items(), key=lambda kv: -kv[1]["longest_consecutive"]):
            print("  %-6s %-10s %-20s"
                  % (pid, v["calls"], v["longest_consecutive"]))
    if rearmed:
        print("")
        print("  EXAMPLE RE-ARM ROWS (timer was already running):")
        for r in rearmed[:10]:
            print("    f=%-6s pid=%-5s outcome=%-16s state=%-22s "
                  "timer_before=%.3f timer_after=%.3f src=%s"
                  % (r["frame"], r["pid"], r["outcome"], r["machine_state"],
                     r["timer_before"] or 0.0, r["timer_after"] or 0.0,
                     r["target_source"]))

    # ── VERDICT ──
    banner("VERDICT -- measured, not inferred")
    if not led.outcome_calls:
        print("  INCONCLUSIVE: no apply_outcome call was captured.")
    else:
        print("  apply_outcome calls captured      = %d" % len(led.outcome_calls))
        print("  stun sets (timer started)         = %d" % len(
            [s for s in led.set_events
             if str(s["source"]).startswith("apply_outcome")]))
        print("  timer clears observed             = %d" % len(led.clear_events))
        by_bound = {}
        for c in led.clear_events:
            for b in (c.get("boundaries") or ["NONE_OBSERVED"]):
                by_bound[b] = by_bound.get(b, 0) + 1
        top = sorted(by_bound.items(), key=lambda kv: -kv[1])[:1]
        if top:
            print("  dominant clear cause              = %s (%d)"
                  % (top[0][0], top[0][1]))
        if held:
            print("  hold duration median / max (s)    = %.2f / %.2f"
                  % (held_sorted[len(held_sorted) // 2] * DT,
                     held_sorted[-1] * DT))
            print("  configured duration (s)           = 0.50 / 0.20")
            excess = held_sorted[-1] * DT - 0.5
            print("  max hold exceeds 0.5 s by         = %.2f s" % excess)
        print("  latched body-frames               = %d"
              % len(led.latched_rows))
        print("  soft-frozen body-frames           = %d"
              % len(led.soft_frozen_rows))
    print("")
    print("  Report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    # ── PHASE 1: THE ROLLOUT, WITH THE ENGINE'S OWN CHATTER DISCARDED ──
    # The engine prints a per-frame defence/fullback audit during KICKOFF.
    # Left attached it both floods the report and dominates the runtime, so
    # the rollout runs with stdout on the null device.  The report is
    # therefore written into a CLEAN file afterwards, never mixed with it.
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

    # ── PHASE 2: THE REPORT, INTO ITS OWN FILE AND TO THE TERMINAL ──
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = tee()
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
