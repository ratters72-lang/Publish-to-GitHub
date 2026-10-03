"""tmp/probe_phase_timer_budget.py - timer vs movement attribution for the
tackle-exit chain.

HYPOTHESIS UNDER TEST
---------------------
"The remaining lag is timer-bound, not movement-bound.  All actors are in
position before PTB, so a tackle-phase CLOCK or GATE is consuming time after
convergence is complete."

This probe measures, per tackle cycle, the exact duration of every phase on
the chain

    TACKLE_CONTACT -> TACKLE_DRIVE -> TACKLE_DOMINANCE -> RELEASE
                   -> PTB_PREP -> PTB_EXEC -> PTB

together with the timer value and the binding condition at each exit, and
splits the RELEASE window into its two possible causes:

    movement/readiness : time until the DH readiness predicate passes
    clock              : remaining time until the time gate (max(1.54,
                         tes_outcome.ptb_time_s)) expires
    residual           : frame quantisation / late re-entry into a gate

METHOD
------
Read-only.  Boots the real engine headless (same bootstrap as
probe_release_gate.py / probe_dh_preposition.py), steps the match, and
records one observation per frame.  Phase boundaries are detected by the
engine's own `_phase` label changing; because `OpenPlayEngine._set_phase()`
resets `_phase_timer = 0.0` on every entry, each phase is measured on a
fresh clock.

The DH readiness predicate is MIRRORED from
`OpenPlayEngine._check_dh_ready_for_ptb()` (all eight conditions, collected
individually rather than short-circuited, so the blocking condition can be
attributed).

VARIANTS (all in memory; no production file is modified)
--------------------------------------------------------
  A  baseline
  B  RELEASE time-gate floor 1.54 s -> `_PROBE_RELEASE_FLOOR` (default 0.0)
  C  DOMINANCE hold (WEAK/NEUTRAL/DOMINANT) -> 0.05 s
  D  B and C together

Variant B patches the literal floor inside `_update_release` by re-compiling
that method's source with the literal replaced by a module global.  If that
patch cannot be installed the variant is skipped and the analytic floor
contribution (1.54 - ptb_time_s when ptb_time_s < 1.54) is still reported.

Run:  python tmp/probe_phase_timer_budget.py
"""

import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import inspect
import io
import math
import sys
import textwrap
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

import game.tes.tes_drive_variation as tdv_mod

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

DT = 1.0 / 60.0
MAX_FRAMES = 3600
ROLLOUTS = 3

# The chain under test, in order.
TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")
CHAIN = TES_PHASES + ("RELEASE",) + PTB_PHASES

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)

# The literal floor the RELEASE time gate applies, restated for the analytic
# decomposition (open_play_engine._update_release:
#     ptb_t = max(1.54, self.tes_outcome.ptb_time_s ...)
RELEASE_FLOOR_LITERAL = 1.54

# The floor actually in force for the run being recorded.  Baseline equals the
# literal above; variant B lowers it.  The probe measures the gate the engine
# is really applying, not the source constant.
EFFECTIVE_RELEASE_FLOOR = RELEASE_FLOOR_LITERAL


# ─────────────────────────────────────────────────────────────────────
# BOOTSTRAP (mirrors the other probes)
# ─────────────────────────────────────────────────────────────────────

def boot_world():
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system


# ─────────────────────────────────────────────────────────────────────
# GATE MIRROR - OpenPlayEngine._check_dh_ready_for_ptb()
# ─────────────────────────────────────────────────────────────────────

def dh_gate_fails(ope, world, for_exec=False):
    """Every failing condition of the DH readiness predicate, not the first."""
    dh = ope.dh
    bc = world.ball_carrier
    fails = []
    if dh is None:
        return ["A_dh_none"]
    if bc is None:
        return ["B_bc_none"]
    ruck_y = getattr(world, "ruck_y_m", bc.y)
    if dh.x >= bc.x + 0.3:
        fails.append("3_ahead_of_bc")
    if abs(dh.x - (bc.x - ope_mod.DH_BEHIND_DISTANCE_M)) > \
            ope_mod.DH_POSITION_TOLERANCE_M + 0.1:
        fails.append("4_pos_err")
    if ruck_y is not None and abs(dh.y - ruck_y) > \
            ope_mod.DH_LATERAL_TOLERANCE_M + 0.3:
        fails.append("5_lateral")
    threshold = (ope_mod.DH_VELOCITY_STABLE_EXEC_THRESHOLD_MPS if for_exec
                 else ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS)
    if math.hypot(dh.vx, dh.vy) > threshold:
        fails.append("6_velocity")
    if bool(getattr(dh, "frozen", False)):
        fails.append("7_frozen")
    if getattr(dh, "defender_role", None) in BLOCKING_ROLES:
        fails.append("8_role")
    return fails


def dh_snapshot(ope, world):
    """Position/velocity state of the dummy half plus its gap to the target."""
    dh = ope.dh
    bc = world.ball_carrier
    if dh is None:
        return {"dh": None}
    row = {
        "dh_id": getattr(dh, "player_id", None),
        "dh_x": float(dh.x),
        "dh_y": float(dh.y),
        "dh_speed": math.hypot(dh.vx, dh.vy),
        "dh_frozen": bool(getattr(dh, "frozen", False)),
        "dh_role": getattr(getattr(dh, "defender_role", None), "name", None),
    }
    if bc is not None:
        row["dh_gap"] = math.hypot(
            (bc.x - ope_mod.DH_BEHIND_DISTANCE_M) - dh.x, bc.y - dh.y)
    return row


def marker_snapshot(ope, world):
    """Marker bodies: distance from each marker to its ruck-relative target."""
    out = []
    ruck_x = getattr(world, "ruck_x_m", None)
    ruck_y = getattr(world, "ruck_y_m", None)
    getters = (
        ("MARKER_A", ope_mod.MARKER_A_DEPTH_M),
        ("MARKER_B", ope_mod.MARKER_B_DEPTH_M),
    )
    try:
        markers = list(ope._get_markers())
    except Exception:
        markers = []
    for idx, (name, depth) in enumerate(getters):
        body = markers[idx] if idx < len(markers) else None
        if body is None:
            out.append({"name": name, "present": False})
            continue
        entry = {
            "name": name,
            "present": True,
            "x": float(body.x),
            "y": float(body.y),
        }
        if ruck_x is not None:
            entry["x_err"] = abs(float(body.x) - (float(ruck_x) + depth))
        if ruck_y is not None:
            entry["y_err"] = abs(float(body.y) - float(ruck_y))
        out.append(entry)
    return out


def bc_pos(world):
    bc = world.ball_carrier
    if bc is None:
        return None, None
    return float(bc.x), float(bc.y)
# ─────────────────────────────────────────────────────────────────────
# CYCLE RECORDING
# ─────────────────────────────────────────────────────────────────────

def new_cycle(frame):
    return {
        "start_frame": frame,
        "active": None,
        "spans_by_phase": {},
        "release_frames": [],
        "dominance_result": None,
        "drive_dur": None,
        "dominance_hold_s": None,
        "ptb_time_s": None,
        "broken": False,
        "incomplete": False,
    }


def _on_entry(cur, ope, world, phase, span):
    """Capture the per-phase entry values that drive the exit gate."""
    if phase == "TACKLE_DRIVE":
        # NOTE: `_set_phase` flips the phase label at the END of the previous
        # handler, so the first frame carrying this label can precede this
        # phase's first execution.  The drive metrics are therefore captured
        # in _observe_frame, on the first frame they actually exist.
        bcx, bcy = bc_pos(world)
        span["bc_entry"] = (bcx, bcy)
    elif phase == "TACKLE_DOMINANCE":
        ctx = ope.tes_ctx
        if ctx is not None:
            cur["dominance_hold_s"] = float(
                getattr(ctx, "dominance_hold_s", 0.25))
        bcx, bcy = bc_pos(world)
        span["bc_entry"] = (bcx, bcy)
    elif phase in ("PTB_PREP", "PTB_EXEC"):
        # PTBManager carries its OWN internal timer.  `_enter_ptb_prep` only
        # calls start_prep() when the manager state is "idle", so a stale
        # state/timer from a previous cycle would skip the reset and let
        # update_prep() return ready immediately.  Record what we inherit.
        span["pm_state_entry"] = str(getattr(ope.ptb_manager, "state", None))
        span["pm_timer_entry"] = float(getattr(ope.ptb_manager, "timer", 0.0))
    elif phase == "RELEASE":
        out = ope.tes_outcome
        if out is not None:
            cur["ptb_time_s"] = float(out.ptb_time_s)
            span["ptb_time_s"] = float(out.ptb_time_s)
            span["ptb_t"] = max(EFFECTIVE_RELEASE_FLOOR,
                                float(out.ptb_time_s))
        span["dh_entry"] = dh_snapshot(ope, world)
        span["markers_entry"] = marker_snapshot(ope, world)
        span["ruck"] = (getattr(world, "ruck_x_m", None),
                        getattr(world, "ruck_y_m", None))
        bcx, bcy = bc_pos(world)
        span["bc_entry"] = (bcx, bcy)


def _observe_frame(cur, ope, world, phase, frame, timer):
    span = cur["active"]
    if span is None or span["phase"] != phase:
        span = {
            "phase": phase,
            "entry_frame": frame,
            "entry_timer": timer,
            "frames": 0,
            "last_timer": timer,
            "last_frame": frame,
        }
        cur["spans_by_phase"].setdefault(phase, []).append(span)
        cur["active"] = span
        _on_entry(cur, ope, world, phase, span)

    span["frames"] += 1
    span["last_timer"] = timer
    span["last_frame"] = frame

    if phase == "TACKLE_DRIVE":
        ctx = ope.tes_ctx
        out = ope.tes_outcome
        # Capture the drive metadata on the first frame it exists (the label
        # can lead the handler by one frame).
        if cur.get("dominance_result") is None and ctx is not None:
            dom = getattr(ctx, "dominance_result", None)
            if dom is not None:
                cur["dominance_result"] = dom
                if out is not None:
                    cur["drive_dur"] = float(out.tackle_duration_s or 0.0)
        if ctx is not None:
            span["drive_elapsed"] = float(
                getattr(ctx, "drive_elapsed_s", 0.0) or 0.0)
        span["bc_last"] = bc_pos(world)
    elif phase == "TACKLE_DOMINANCE":
        ctx = ope.tes_ctx
        if ctx is not None:
            span["dominance_elapsed"] = float(
                getattr(ctx, "dominance_elapsed_s", 0.0) or 0.0)
        span["bc_last"] = bc_pos(world)
    elif phase == "RELEASE":
        fails = dh_gate_fails(ope, world, for_exec=False)
        span["dh_last"] = dh_snapshot(ope, world)
        span["markers_last"] = marker_snapshot(ope, world)
        span["bc_last"] = bc_pos(world)
        cur["release_frames"].append({
            "frame": frame,
            "t": timer,
            "fails": fails,
            "dh_ok": not fails,
            "override": int(getattr(ope, "_ptb_gate_override_frames", 999999)),
        })


def _close_cycle(cur, frame, incomplete=False):
    cur["end_frame"] = frame
    cur["incomplete"] = incomplete
    # A cycle is "broken" when it left the chain before RELEASE was reached.
    cur["broken"] = "RELEASE" not in cur["spans_by_phase"]
    return cur


def run_rollout():
    """Step one match; return the list of recorded tackle cycles."""
    cycles = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()
        prev_phase = None
        cur = None
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(DT)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    ope.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = ope._phase
            frame = int(world.frame_count)
            timer = float(ope._phase_timer)

            if cur is None and phase in TES_PHASES:
                cur = new_cycle(frame)

            if cur is not None:
                _observe_frame(cur, ope, world, phase, frame, timer)
                if phase not in CHAIN:
                    cycles.append(_close_cycle(cur, frame))
                    cur = None

            prev_phase = phase

        if cur is not None:
            cycles.append(_close_cycle(cur, int(world.frame_count),
                                       incomplete=True))
    return cycles


# ─────────────────────────────────────────────────────────────────────
# PHASE DURATION + RELEASE DECOMPOSITION
# ─────────────────────────────────────────────────────────────────────

def phase_duration(cur, phase):
    """(duration_s, exit_timer_s, frames) for the first span of *phase*."""
    spans = cur["spans_by_phase"].get(phase)
    if not spans:
        return None, None, None
    span = spans[0]
    return span["frames"] * DT, span["last_timer"], span["frames"]


def phase_span(cur, phase):
    spans = cur["spans_by_phase"].get(phase)
    return spans[0] if spans else None


def release_decomposition(cur):
    """Split the RELEASE window into readiness / dead clock / gate overhang."""
    span = phase_span(cur, "RELEASE")
    if span is None:
        return None
    frames = cur["release_frames"]
    t_exit = span["frames"] * DT
    ptb_t = span.get("ptb_t")
    ptb_time_s = span.get("ptb_time_s")
    t_dh_ready = next((r["t"] for r in frames if r["dh_ok"]), None)

    blockers = Counter()
    for r in frames:
        time_ok = ptb_t is not None and r["t"] >= ptb_t
        if not time_ok and r["dh_ok"]:
            blockers["TIME"] += 1
        elif time_ok and not r["dh_ok"]:
            blockers["DH"] += 1
        elif not time_ok and not r["dh_ok"]:
            blockers["BOTH"] += 1
        else:
            blockers["PASS"] += 1

    override_armed = any(r["override"] < 999999 for r in frames)
    override_min = min((r["override"] for r in frames), default=999999)

    # The window the clock consumed AFTER the movement requirement was met.
    if t_dh_ready is None or ptb_t is None:
        dead_clock_s = None
        gate_overhang_s = None
    else:
        dead_clock_s = max(0.0, min(ptb_t, t_exit) - t_dh_ready)
        gate_overhang_s = max(0.0, t_exit - max(ptb_t, t_dh_ready))

    floor_dead_s = (None if ptb_time_s is None
                    else max(0.0, RELEASE_FLOOR_LITERAL - ptb_time_s))

    return {
        "t_exit": t_exit,
        "ptb_t": ptb_t,
        "ptb_time_s": ptb_time_s,
        "t_dh_ready": t_dh_ready,
        "dead_clock_s": dead_clock_s,
        "gate_overhang_s": gate_overhang_s,
        "floor_dead_s": floor_dead_s,
        "blockers": dict(blockers),
        "override_armed": override_armed,
        "override_min": override_min,
        "frames": len(frames),
    }


def dominance_move_metres(cur):
    """How far the BC actually moved during TACKLE_DOMINANCE."""
    span = phase_span(cur, "TACKLE_DOMINANCE")
    if span is None:
        return None
    a, b = span.get("bc_entry"), span.get("bc_last")
    if not a or not b or a[0] is None or b[0] is None:
        return None
    return math.hypot(b[0] - a[0], b[1] - a[1])


def drive_move_metres(cur):
    span = phase_span(cur, "TACKLE_DRIVE")
    if span is None:
        return None
    a, b = span.get("bc_entry"), span.get("bc_last")
    if not a or not b or a[0] is None or b[0] is None:
        return None
    return math.hypot(b[0] - a[0], b[1] - a[1])


def chain_span(cur):
    """DRIVE_END -> PTB_ENTER seconds, plus its component phases."""
    parts = {}
    for ph in ("TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC"):
        dur, _, _ = phase_duration(cur, ph)
        parts[ph] = dur
    if any(v is None for v in parts.values()):
        return None, parts
    return sum(parts.values()), parts


# ─────────────────────────────────────────────────────────────────────
# PAIRED COUNTERFACTUALS
# ─────────────────────────────────────────────────────────────────────
# The A/B/C/D runs are NOT paired: shortening a phase changes how many random
# draws the engine consumes, so every later tackle diverges.  The rigorous
# attribution is therefore computed from the SAME cycle's recorded gate
# values: what the exit WOULD have been with one knob removed.

def counterfactual_report(cur):
    """Per-cycle counterfactual savings, from the recorded gates only."""
    dec = release_decomposition(cur)
    if dec is None:
        return None
    hold = cur.get("dominance_hold_s")
    ready = dec["t_dh_ready"]
    ptb_time_s = dec["ptb_time_s"]
    rel_dur = dec["t_exit"]

    # (a) floor removed: the gate becomes max(dh_ready, ptb_time_s).
    if ready is not None and ptb_time_s is not None:
        exit_without_floor = max(ready, ptb_time_s)
        save_floor = max(0.0, rel_dur - exit_without_floor)
    else:
        save_floor = None

    # (b) dominance hold shortened to 0.05 s.
    save_hold = max(0.0, hold - 0.05) if hold is not None else None

    return {
        "result": cur.get("dominance_result"),
        "rel_dur": rel_dur,
        "ptb_time_s": ptb_time_s,
        "dead_clock_s": dec["dead_clock_s"],
        "save_floor": save_floor,
        "save_hold": save_hold,
        "chain": chain_span(cur)[0],
    }


def print_counterfactual_attribution(name, cycles):
    rows = [counterfactual_report(cur) for cur in cycles]
    rows = [r for r in rows if r is not None]
    print("-" * 118)
    print("PAIRED COUNTERFACTUAL - %s  (same cycles, same gates, no re-run)"
          % name)
    print("-" * 118)
    if not rows:
        print("  (no cycles)")
        print()
        return
    print("  cycles                    : %d" % len(rows))
    print("  dominance outcome mix     : %s"
          % dict(Counter(str(r["result"]) for r in rows)))
    st_dec = stats([r["dead_clock_s"] for r in rows])
    st_f = stats([r["save_floor"] for r in rows])
    st_h = stats([r["save_hold"] for r in rows])
    print()
    print("  per-cycle seconds where every actor is already in position and")
    print("  ONLY a clock is still running:")
    if st_dec:
        print("    RELEASE dead clock         : mean %s  max %s  total %s"
              % (fmt(st_dec["mean"]), fmt(st_dec["max"]),
                 fmt(sum(r["dead_clock_s"] or 0.0 for r in rows))))
    if st_f:
        print("    ...of which the 1.54 floor : mean %s  max %s  total %s"
              % (fmt(st_f["mean"]), fmt(st_f["max"]),
                 fmt(sum(r["save_floor"] or 0.0 for r in rows))))
    if st_h:
        print("    DOMINANCE hold recoverable : mean %s  max %s  total %s"
              % (fmt(st_h["mean"]), fmt(st_h["max"]),
                 fmt(sum(r["save_hold"] or 0.0 for r in rows))))
    total = sum((r["dead_clock_s"] or 0.0) + (r["save_hold"] or 0.0)
                for r in rows)
    print()
    print("    TOTAL recoverable without touching any actor's motion: %s s"
          % fmt(total))
    print("    per tackle                                          : %s s"
          % fmt(total / len(rows)))
    print()
    print("  per-outcome breakdown:")
    by_result = {}
    for r in rows:
        by_result.setdefault(str(r["result"]), []).append(r)
    for key in sorted(by_result):
        group = by_result[key]
        g_dec = stats([r["dead_clock_s"] for r in group])
        g_f = stats([r["save_floor"] for r in group])
        g_h = stats([r["save_hold"] for r in group])
        print("    %-9s n=%2d  release=%s  dead_clock=%s  floor=%s  hold=%s"
              % (key, len(group),
                 fmt(stats([r["rel_dur"] for r in group])["mean"]),
                 fmt(g_dec["mean"]) if g_dec else "-",
                 fmt(g_f["mean"]) if g_f else "-",
                 fmt(g_h["mean"]) if g_h else "-"))
    print()
# ─────────────────────────────────────────────────────────────────────
# IN-MEMORY VARIANTS (no production file is modified)
# ─────────────────────────────────────────────────────────────────────

_ORIG_UPDATE_RELEASE = ope_mod.OpenPlayEngine._update_release
# SUPERSEDED (2026-09-17): this probe was written against the CLOCK-BOUND
# RELEASE model -- it re-compiled _update_release to rewrite the literal
# `ptb_t = max(1.54, ...)`.  That literal, and the RELEASE_MIN_DURATION_S
# wall it came from, are gone: RELEASE now exits on the convergence
# predicate owned by world/rules/release_gate.py, and the `1.54` rewrite
# below no longer finds its target.  The variants are kept so this
# historical probe still imports and runs; the LIVE convergence budget is
# measured by tmp/probe_release_convergence.py.
_RELEASE_FLOOR_ATTR = "RELEASE_MIN_CONVERGENCE_S"
_ORIG_RELEASE_MIN_DURATION_S = getattr(ope_mod, _RELEASE_FLOOR_ATTR, None)
_HOLD_KEYS = ("DOMINANCE_HOLD_WEAK", "DOMINANCE_HOLD_NEUTRAL",
              "DOMINANCE_HOLD_DOMINANT", "DOMINANCE_HOLD_DEFAULT")


def install_release_floor(floor):
    """Replace the literal 1.54 time-gate floor with `_PROBE_RELEASE_FLOOR`.

    `_update_release` computes `ptb_t = max(1.54, tes_outcome.ptb_time_s ...)`
    with a LITERAL, so no module-level patch can reach it.  The method's own
    source is re-compiled with that literal replaced by a module global which
    the caller can then vary.  Returns False when the literal is not found.
    """
    src = textwrap.dedent(inspect.getsource(_ORIG_UPDATE_RELEASE))
    lines = src.split("\n")
    hit = False
    for idx, line in enumerate(lines):
        if "ptb_t = max(1.54" in line:
            lines[idx] = line.replace("1.54", "_PROBE_RELEASE_FLOOR")
            hit = True
    if not hit:
        return False
    ope_mod._PROBE_RELEASE_FLOOR = float(floor)
    namespace = ope_mod.__dict__
    exec(compile("\n".join(lines), "<probe_release_floor>", "exec"), namespace)
    ope_mod.OpenPlayEngine._update_release = namespace["_update_release"]
    # Keep the override countdown's start consistent with the gate.
    setattr(ope_mod, _RELEASE_FLOOR_ATTR, float(floor))
    global EFFECTIVE_RELEASE_FLOOR
    EFFECTIVE_RELEASE_FLOOR = float(floor)
    return True


def restore_release_floor():
    ope_mod.OpenPlayEngine._update_release = _ORIG_UPDATE_RELEASE
    if _ORIG_RELEASE_MIN_DURATION_S is not None:
        setattr(ope_mod, _RELEASE_FLOOR_ATTR, _ORIG_RELEASE_MIN_DURATION_S)
    global EFFECTIVE_RELEASE_FLOOR
    EFFECTIVE_RELEASE_FLOOR = RELEASE_FLOOR_LITERAL
    if hasattr(ope_mod, "_PROBE_RELEASE_FLOOR"):
        del ope_mod._PROBE_RELEASE_FLOOR


def install_short_hold(value=0.05):
    """Shorten the DOMINANCE hold (read at call time by dominance_hold_for)."""
    saved = {key: getattr(tdv_mod, key) for key in _HOLD_KEYS}
    for key in _HOLD_KEYS:
        setattr(tdv_mod, key, float(value))
    return saved


def restore_hold(saved):
    for key, value in saved.items():
        setattr(tdv_mod, key, value)


# ─────────────────────────────────────────────────────────────────────
# AGGREGATION HELPERS
# ─────────────────────────────────────────────────────────────────────

def stats(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {
        "n": n,
        "min": vals[0],
        "median": vals[n // 2],
        "mean": sum(vals) / n,
        "max": vals[-1],
    }


def fmt(value, nd=2):
    if value is None:
        return "-"
    try:
        return ("%." + str(nd) + "f") % float(value)
    except (TypeError, ValueError):
        return str(value)


def complete_cycles(cycles):
    """Cycles that traversed the whole chain to PTB."""
    out = []
    for cur in cycles:
        if cur.get("broken") or cur.get("incomplete"):
            continue
        if all(cur["spans_by_phase"].get(p) for p in
               ("TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC")):
            out.append(cur)
    return out


def print_cycle_table(name, cycles):
    print("-" * 118)
    print("PER-CYCLE BOUNDARY DURATIONS (seconds) - %s" % name)
    print("-" * 118)
    hdr = ("  #  dom       dur  contact   drive    domin  release"
           "    prep    exec      ptb | chain  ptb_t dh_ready   dead  over")
    print(hdr)
    print("-" * 118)
    for i, cur in enumerate(cycles):
        spans = cur["spans_by_phase"]
        row = []
        for ph in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
                   "TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC",
                   "PTB"):
            dur, _, _ = phase_duration(cur, ph)
            row.append(dur)
        chain, _ = chain_span(cur)
        dec = release_decomposition(cur) or {}
        print("%3d %-9s %8s %8s %8s %8s %9s %7s %7s %8s | %5s %6s %8s %6s %5s" % (
            i,
            str(cur.get("dominance_result")),
            fmt(cur.get("drive_dur")),
            fmt(row[1]),
            fmt(row[2]),
            fmt(row[3]),
            fmt(row[4]),
            fmt(row[5]),
            fmt(row[6]),
            fmt(row[7]),
            fmt(chain),
            fmt(dec.get("ptb_t")),
            fmt(dec.get("t_dh_ready")),
            fmt(dec.get("dead_clock_s")),
            fmt(dec.get("gate_overhang_s")),
        ))
    print()


def print_phase_ledger(name, cycles):
    print("-" * 118)
    print("PHASE DURATION LEDGER - %s  (%d complete cycles)" % (name, len(cycles)))
    print("-" * 118)
    print("  %-18s %4s %8s %8s %8s %8s" % (
        "phase", "n", "min", "median", "mean", "max"))
    for ph in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
               "TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC",
               "PTB", "POST_RUCK_OPEN_PLAY"):
        values = [phase_duration(cur, ph)[0] for cur in cycles]
        st = stats(values)
        if st is None:
            print("  %-18s %4d %8s %8s %8s %8s" % (ph, 0, "-", "-", "-", "-"))
            continue
        print("  %-18s %4d %8s %8s %8s %8s" % (
            ph, st["n"], fmt(st["min"]), fmt(st["median"]),
            fmt(st["mean"]), fmt(st["max"])))
    print()
    chain_stats = stats([chain_span(cur)[0] for cur in cycles])
    if chain_stats:
        print("  DRIVE_END -> PTB_ENTER (DOMINANCE + RELEASE + PREP + EXEC)")
        print("    n=%d  min=%s  median=%s  mean=%s  max=%s"
              % (chain_stats["n"], fmt(chain_stats["min"]),
                 fmt(chain_stats["median"]), fmt(chain_stats["mean"]),
                 fmt(chain_stats["max"])))
    print()


def print_release_attribution(name, cycles):
    decs = [release_decomposition(cur) for cur in cycles]
    decs = [d for d in decs if d is not None]
    print("-" * 118)
    print("RELEASE DECOMPOSITION - %s" % name)
    print("-" * 118)
    if not decs:
        print("  (no completed RELEASE windows)")
        print()
        return
    exits = stats([d["t_exit"] for d in decs])
    clocks = stats([d["dead_clock_s"] for d in decs])
    readys = stats([d["t_dh_ready"] for d in decs])
    over = stats([d["gate_overhang_s"] for d in decs])
    floors = stats([d["floor_dead_s"] for d in decs])
    print("  RELEASE duration             : n=%d min=%s med=%s mean=%s max=%s" % (
        exits["n"], fmt(exits["min"]), fmt(exits["median"]),
        fmt(exits["mean"]), fmt(exits["max"])))
    if readys:
        print("  movement/readiness (dh ready): n=%d min=%s med=%s mean=%s max=%s" % (
            readys["n"], fmt(readys["min"]), fmt(readys["median"]),
            fmt(readys["mean"]), fmt(readys["max"])))
    if clocks:
        print("  DEAD CLOCK after dh ready    : n=%d min=%s med=%s mean=%s max=%s" % (
            clocks["n"], fmt(clocks["min"]), fmt(clocks["median"]),
            fmt(clocks["mean"]), fmt(clocks["max"])))
    if over:
        print("  gate overhang past the clock : n=%d min=%s med=%s mean=%s max=%s" % (
            over["n"], fmt(over["min"]), fmt(over["median"]),
            fmt(over["mean"]), fmt(over["max"])))
    if floors:
        print("  floor dead time (1.54-ptb_t) : n=%d min=%s med=%s mean=%s max=%s" % (
            floors["n"], fmt(floors["min"]), fmt(floors["median"]),
            fmt(floors["mean"]), fmt(floors["max"])))
    print()
    print("  per-frame blocker classification (frame counts over all cycles):")
    total = Counter()
    for d in decs:
        for key, count in d["blockers"].items():
            total[key] += count
    frames = sum(total.values())
    for key in ("TIME", "DH", "BOTH", "PASS"):
        count = total.get(key, 0)
        pct = (100.0 * count / frames) if frames else 0.0
        print("    %-6s %6d  (%5.1f%%)" % (key, count, pct))
    print()
    print("  cycles whose RELEASE was held by the CLOCK after the DH was ready:")
    dead = [d for d in decs if (d["dead_clock_s"] or 0.0) > 0.0]
    over = [d for d in decs if (d["gate_overhang_s"] or 0.0) > 0.0]
    print("    dead-clock cycles  : %d / %d" % (len(dead), len(decs)))
    print("    gate-overhang cycles: %d / %d" % (len(over), len(decs)))
    never = [d for d in decs if d["t_dh_ready"] is None]
    print("    DH never ready      : %d / %d" % (len(never), len(decs)))
    armed = [d for d in decs if d["override_armed"]]
    print("    override armed      : %d / %d" % (len(armed), len(decs)))
    print()
    print("  mean RELEASE split by dominant result:")
    by_result = {}
    for cur, dec in zip(cycles, decs):
        key = str(cur.get("dominance_result"))
        by_result.setdefault(key, []).append(dec)
    for key in sorted(by_result):
        group = by_result[key]
        print("    %-9s n=%2d  exit=%s  ptb_t=%s  dh_ready=%s  dead=%s" % (
            key, len(group),
            fmt(stats([d["t_exit"] for d in group])["mean"]),
            fmt(stats([d["ptb_t"] for d in group])["mean"]),
            fmt(stats([d["t_dh_ready"] for d in group])["mean"]),
            fmt(stats([d["dead_clock_s"] for d in group])["mean"]),
        ))
    print()
def print_dominance_audit(name, cycles):
    """Show that DOMINANCE carries almost no displacement per second."""
    moves = [dominance_move_metres(cur) for cur in cycles]
    moves = [m for m in moves if m is not None]
    holds = [cur.get("dominance_hold_s") for cur in cycles]
    holds = [h for h in holds if h is not None]
    drive_moves = [drive_move_metres(cur) for cur in cycles]
    drive_moves = [m for m in drive_moves if m is not None]
    print("-" * 118)
    print("MOVEMENT CONTENT PER PHASE - %s" % name)
    print("-" * 118)
    st = stats(moves)
    if st:
        print("  BC displacement during TACKLE_DOMINANCE (m):"
              " n=%d min=%s med=%s mean=%s max=%s" % (
                  st["n"], fmt(st["min"], 3), fmt(st["median"], 3),
                  fmt(st["mean"], 3), fmt(st["max"], 3)))
    hs = stats(holds)
    if hs:
        print("  dominance_hold_s                            :"
              " n=%d min=%s med=%s mean=%s max=%s" % (
                  hs["n"], fmt(hs["min"]), fmt(hs["median"]),
                  fmt(hs["mean"]), fmt(hs["max"])))
        if st and hs["mean"] > 0:
            print("  => mean BC speed during DOMINANCE            : %s m/s" % (
                fmt(st["mean"] / hs["mean"], 3)))
    ds = stats(drive_moves)
    if ds:
        print("  BC displacement during TACKLE_DRIVE (m)     :"
              " n=%d min=%s med=%s mean=%s max=%s" % (
                  ds["n"], fmt(ds["min"]), fmt(ds["median"]),
                  fmt(ds["mean"]), fmt(ds["max"])))
    print()


def print_ptb_entry_audit(name, cycles):
    """Why PTB_PREP can be shorter than PTBManager's _DH_PREP_MIN_TIME_S."""
    prep = []
    exec_ = []
    for cur in cycles:
        s = phase_span(cur, "PTB_PREP")
        if s is not None:
            prep.append((s.get("pm_state_entry"), s.get("pm_timer_entry"),
                         s["frames"] * DT))
        s = phase_span(cur, "PTB_EXEC")
        if s is not None:
            exec_.append((s.get("pm_state_entry"), s.get("pm_timer_entry"),
                          s["frames"] * DT))
    print("-" * 118)
    print("PTB_PREP / PTB_EXEC ENTRY STATE - %s" % name)
    print("-" * 118)
    print("  PTBManager._DH_PREP_MIN_TIME_S = %s s   _DH_EXEC_MIN_TIME_S = %s s"
          % (fmt(_DH_PREP_MIN_TIME_S), fmt(_DH_EXEC_MIN_TIME_S)))
    for label, rows in (("PTB_PREP", prep), ("PTB_EXEC", exec_)):
        if not rows:
            continue
        states = Counter(str(r[0]) for r in rows)
        durations = stats([r[2] for r in rows])
        timers = stats([r[1] for r in rows])
        print("  %-9s manager state at entry : %s" % (label, dict(states)))
        print("            manager timer at entry : mean %s  max %s"
              % (fmt(timers["mean"]), fmt(timers["max"])))
        print("            phase duration         : min %s  mean %s  max %s"
              % (fmt(durations["min"]), fmt(durations["mean"]),
                 fmt(durations["max"])))
        short = [r for r in rows if r[2] < _DH_PREP_MIN_TIME_S]
        print("            shorter than the min   : %d / %d"
              % (len(short), len(rows)))
        if short:
            print("            inherited timer values : %s"
                  % dict(Counter(fmt(r[1]) for r in short)))
    print()


_DH_PREP_MIN_TIME_S = 0.18
_DH_EXEC_MIN_TIME_S = 0.15


def print_static_reference():
    """The constants the chain is built from, read from the live modules."""
    print("-" * 118)
    print("STATIC REFERENCE - the timers the chain is built from")
    print("-" * 118)
    print("  RELEASE time-gate floor (literal in _update_release) : %s s"
          % fmt(RELEASE_FLOOR_LITERAL))
    print("  RELEASE gap floor (release_gate, was MIN_DURATION_S)  : %s s"
          % fmt(getattr(ope_mod, _RELEASE_FLOOR_ATTR, None)))
    print("  RELEASE_PAUSE_S (defined, never referenced)          : %s s"
          % fmt(getattr(ope_mod, "RELEASE_PAUSE_S", None)))
    print("  DH_BEHIND_DISTANCE_M                                 : %s m"
          % fmt(ope_mod.DH_BEHIND_DISTANCE_M))
    print("  DH_POSITION_TOLERANCE_M (+0.1 in gate)               : %s m"
          % fmt(ope_mod.DH_POSITION_TOLERANCE_M))
    print("  DH_LATERAL_TOLERANCE_M (+0.3 in gate)                : %s m"
          % fmt(ope_mod.DH_LATERAL_TOLERANCE_M))
    print("  DH_VELOCITY_STABLE_THRESHOLD_MPS (release / prep)     : %s m/s"
          % fmt(ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS))
    print("  DH_VELOCITY_STABLE_EXEC_THRESHOLD_MPS (exec)          : %s m/s"
          % fmt(ope_mod.DH_VELOCITY_STABLE_EXEC_THRESHOLD_MPS))
    print("  MARKER_A_DEPTH_M / MARKER_B_DEPTH_M                   : %s / %s m"
          % (fmt(ope_mod.MARKER_A_DEPTH_M), fmt(ope_mod.MARKER_B_DEPTH_M)))
    print("  MARKER_MOVE_SPEED_MPS                                 : %s m/s"
          % fmt(ope_mod.MARKER_MOVE_SPEED_MPS))
    print("  DRIVE_DURATION_* (WEAK / NEUTRAL / DOMINANT)          : %s / %s / %s"
          % (fmt(ope_mod.DRIVE_DURATION_WEAK),
             fmt(ope_mod.DRIVE_DURATION_NEUTRAL),
             fmt(ope_mod.DRIVE_DURATION_DOMINANT)))
    print("  DOMINANCE_HOLD_* (WEAK / NEUTRAL / DOMINANT)          : %s / %s / %s s"
          % (fmt(tdv_mod.DOMINANCE_HOLD_WEAK),
             fmt(tdv_mod.DOMINANCE_HOLD_NEUTRAL),
             fmt(tdv_mod.DOMINANCE_HOLD_DOMINANT)))
    print("  RELEASE phase timer reset on every _set_phase        : True")
    print()
    print("  analytic worst/best case for DRIVE_END -> PTB_ENTER")
    print("    (dominance hold + max(1.54, ptb_time_s) + prep + exec)")
    prep_min = 0.18
    exec_min = 0.15
    for result, hold, ptb_lo, ptb_hi in (
            ("DOMINANT", tdv_mod.DOMINANCE_HOLD_DOMINANT, 1.0, 1.4),
            ("NEUTRAL", tdv_mod.DOMINANCE_HOLD_NEUTRAL, 1.6, 2.4),
            ("WEAK", tdv_mod.DOMINANCE_HOLD_WEAK, 2.2, 3.2)):
        lo = hold + max(RELEASE_FLOOR_LITERAL, ptb_lo) + prep_min + exec_min
        hi = hold + max(RELEASE_FLOOR_LITERAL, ptb_hi) + prep_min + exec_min
        floor_waste = max(0.0, RELEASE_FLOOR_LITERAL - ptb_lo)
        print("    %-9s : %.2f .. %.2f s   (floor waste >= %.2f s)"
              % (result, lo, hi, floor_waste))
    print()


# ─────────────────────────────────────────────────────────────────────
# VARIANT RUNNER
# ─────────────────────────────────────────────────────────────────────

VARIANTS = (
    ("A", "baseline", False, False),
    ("B", "RELEASE floor 1.54 -> 0.00 s", True, False),
    ("C", "DOMINANCE hold -> 0.05 s", False, True),
    ("D", "B + C together", True, True),
)


def run_variant(floor_on, hold_on):
    """Run ROLLOUTS matches under one variant; None when unusable."""
    saved_hold = None
    cycles = []
    try:
        if floor_on:
            if not install_release_floor(0.0):
                return None
        if hold_on:
            saved_hold = install_short_hold(0.05)
        for _ in range(ROLLOUTS):
            cycles.extend(run_rollout())
    finally:
        if floor_on:
            restore_release_floor()
        if saved_hold is not None:
            restore_hold(saved_hold)
    return cycles


def print_variant_comparison(results):
    print("=" * 118)
    print("A/B/C/D - WHAT THE FLOOR AND THE HOLD ACTUALLY COST")
    print("=" * 118)
    print("  NOTE: these runs are NOT paired - shortening a phase changes how many")
    print("  RNG draws the engine consumes, so later tackles diverge.  The paired")
    print("  counterfactual above is the authoritative attribution; treat the")
    print("  'vs A' deltas here as indicative only.")
    print()
    print("  %-4s %-32s %4s %10s %10s %10s %8s %8s" % (
        "var", "knob changed", "n", "chain_mean", "rel_mean",
        "dom_mean", "prep", "exec"))
    print("-" * 118)
    baseline_chain = None
    for tag, label, _, _ in VARIANTS:
        cycles = results.get(tag)
        if not cycles:
            print("  %-4s %-32s   (variant not applied)" % (tag, label))
            continue
        done = complete_cycles(cycles)
        if not done:
            print("  %-4s %-32s   (no complete cycles)" % (tag, label))
            continue
        chain = stats([chain_span(cur)[0] for cur in done])["mean"]
        rel = stats([phase_duration(cur, "RELEASE")[0]
                     for cur in done])["mean"]
        dom = stats([phase_duration(cur, "TACKLE_DOMINANCE")[0]
                     for cur in done])["mean"]
        prep = stats([phase_duration(cur, "PTB_PREP")[0]
                      for cur in done])["mean"]
        exec_ = stats([phase_duration(cur, "PTB_EXEC")[0]
                       for cur in done])["mean"]
        if tag == "A":
            baseline_chain = chain
        delta = "" if baseline_chain is None or tag == "A" else (
            "  (%+.2f s vs A)" % (chain - baseline_chain))
        print("  %-4s %-32s %4d %10s %10s %10s %8s %8s%s" % (
            tag, label, len(done), fmt(chain), fmt(rel), fmt(dom),
            fmt(prep), fmt(exec_), delta))
    print()


def main():
    print("=" * 118)
    print("TACKLE-EXIT CHAIN: TIMER vs MOVEMENT ATTRIBUTION")
    print("  DRIVE_END -> DOMINANCE_END -> RELEASE_ENTER -> RELEASE_EXIT")
    print("            -> PTB_ENTER")
    print("=" * 118)
    print("  frames per rollout : %d   rollouts per variant : %d   dt : %.6f"
          % (MAX_FRAMES, ROLLOUTS, DT))
    print("  DH preposition writer present: %s"
          % hasattr(ope_mod, "DH_PREPOSITION_SPEED_MPS"))
    print()

    print_static_reference()

    results = {}
    for tag, label, floor_on, hold_on in VARIANTS:
        results[tag] = run_variant(floor_on, hold_on)

    a_cycles = results.get("A") or []
    if not a_cycles:
        print("RESULT: no tackle cycles recorded - nothing to attribute.")
        return 2

    a_done = complete_cycles(a_cycles)
    print("=" * 118)
    print("VARIANT A - BASELINE DETAIL")
    print("=" * 118)
    print("  cycles recorded      : %d" % len(a_cycles))
    print("  cycles reaching PTB  : %d" % len(a_done))
    print("  broken / incomplete  : %d" % (len(a_cycles) - len(a_done)))
    print()
    print_cycle_table("A (baseline)", a_done)
    print_phase_ledger("A (baseline)", a_done)
    print_release_attribution("A (baseline)", a_done)
    print_dominance_audit("A (baseline)", a_done)
    print_counterfactual_attribution("A (baseline)", a_done)
    print_ptb_entry_audit("A (baseline)", a_done)

    print_variant_comparison(results)

    print("=" * 118)
    print("VERDICT")
    print("=" * 118)
    decs = [release_decomposition(cur) for cur in a_done]
    decs = [d for d in decs if d]
    if decs:
        dead = stats([d["dead_clock_s"] for d in decs])
        over = stats([d["gate_overhang_s"] for d in decs])
        ready = stats([d["t_dh_ready"] for d in decs])
        total_dead = sum(d["dead_clock_s"] or 0.0 for d in decs)
        total_over = sum(d["gate_overhang_s"] or 0.0 for d in decs)
        total_ready = sum(d["t_dh_ready"] or 0.0 for d in decs)
        print("  RELEASE, summed over %d baseline cycles:" % len(decs))
        print("    movement/readiness (waiting for the DH) : %s s" % fmt(total_ready))
        print("    dead clock (DH in position, clock open) : %s s" % fmt(total_dead))
        print("    gate overhang past the clock             : %s s" % fmt(total_over))
        if dead and ready:
            print("    mean dead clock per cycle                : %s s"
                  % fmt(dead["mean"]))
        if ready:
            print("    mean readiness per cycle                 : %s s"
                  % fmt(ready["mean"]))
    dom = stats([phase_duration(cur, "TACKLE_DOMINANCE")[0] for cur in a_done])
    if dom:
        print("  TACKLE_DOMINANCE: mean %s s per cycle, pure hold clock "
              "(no arrival condition in the exit test)" % fmt(dom["mean"]))
    prep = stats([phase_duration(cur, "PTB_PREP")[0] for cur in a_done])
    exec_ = stats([phase_duration(cur, "PTB_EXEC")[0] for cur in a_done])
    if prep and exec_:
        print("  PTB_PREP / PTB_EXEC: mean %s / %s s per cycle"
              % (fmt(prep["mean"]), fmt(exec_["mean"])))
    chain = stats([chain_span(cur)[0] for cur in a_done])
    if chain:
        print("  DRIVE_END -> PTB_ENTER: mean %s s per cycle" % fmt(chain["mean"]))
    print()
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
