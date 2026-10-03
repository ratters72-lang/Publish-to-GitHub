"""tmp/probe_release_convergence.py -- RELEASE convergence-budget probe.

PURPOSE
-------
The timer-budget report measured only ONE of the RELEASE-owned readiness
requirements: the DH predicate.  It did not measure the other two movers
that RELEASE owns and whose ARRIVAL POSITION matters:

    - MARKER_A / MARKER_B  (ex-tacklers walking to their ruck-pin targets)
    - the ball carrier     (recovering to its ruck-relative recovery target)

The DEF_RETREAT_10 line is deliberately NOT part of the RELEASE exit
contract (it keeps retreating through the whole PTB era by design), so it
is measured for information only and never gates anything.

For every complete tackle cycle this probe records, per RELEASE frame:

    timer, dh_ready, marker A/B position error, BC position error,
    BC speed

and reports, per cycle:

    t_dh_ready      first timer at which the DH predicate passed
    t_markers_ready first timer at which BOTH markers were within tol
    t_bc_ready      first timer at which the BC was within tol of its
                    recovery target (or stopped)
    t_all_ready     first timer at which all three were simultaneously true
    release_exit    the timer the engine actually exited RELEASE on

The gap `release_exit - t_all_ready` is the dead clock a convergence-first
model would remove.  `t_markers_ready` is the number that sizes the new
RELEASE floor: if markers are the binding term, the achievable RELEASE
duration is their travel time (at MARKER_MOVE_SPEED_MPS), not the
max(1.54, ptb_time_s) clock.

READ-ONLY.  No production file is modified and no engine state is patched.

Run:  python tmp/probe_release_convergence.py
"""

import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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
ROLLOUTS = 4

TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")
CHAIN = TES_PHASES + ("RELEASE",) + PTB_PHASES
RELEASE_TAIL = ("RELEASE",) + PTB_PHASES

# Convergence tolerances under test (metres).  The probe reports the raw
# error distribution as well, so these can be re-sized from the data.
MARKER_TOLERANCE_M = 0.25
BC_POSITION_TOLERANCE_M = 0.20
BC_SPEED_TOLERANCE_MPS = 0.30

# MARKER movement authority is granted at RELEASE entry; the writer speed
# bounds the earliest possible convergence.
MARKER_SPEED_MPS = float(getattr(ope_mod, "MARKER_MOVE_SPEED_MPS", 4.0))

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)


# ---------------------------------------------------------------------
# BOOTSTRAP (mirrors the other probes)
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# COMPONENT MEASUREMENT
# ---------------------------------------------------------------------

def dh_gate_fails(ope, world, for_exec=False):
    """Every failing condition of the DH readiness predicate."""
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


def marker_errors(ope):
    """[(name, error_m, tx, ty)] for each present marker, or []."""
    out = []
    try:
        markers = list(ope._get_markers())
    except Exception:
        return out
    for body in markers:
        role = getattr(body, "defender_role", None)
        name = getattr(role, "name", str(role))
        target = None
        try:
            target = ope._get_marker_target(body)
        except Exception:
            target = None
        if target is None:
            out.append((name, None, None, None))
            continue
        tx, ty = float(target[0]), float(target[1])
        err = math.hypot(tx - float(body.x), ty - float(body.y))
        out.append((name, err, tx, ty))
        out[-1] = out[-1]
    # Preserve MARKER_A before MARKER_B order.
    out.sort(key=lambda row: row[0])
    return out


def bc_error(world):
    """BC error to its RELEASE recovery target + its speed, or (None, None)."""
    bc = world.ball_carrier
    if bc is None:
        return None, None
    tx = getattr(bc, "target_x", None)
    ty = getattr(bc, "target_y", None)
    if tx is None or ty is None:
        return None, math.hypot(bc.vx, bc.vy)
    return math.hypot(float(tx) - bc.x, float(ty) - bc.y), math.hypot(bc.vx, bc.vy)


# ---------------------------------------------------------------------
# CYCLE RECORDING
# ---------------------------------------------------------------------

def new_cycle(frame, dominance_result):
    return {
        "start_frame": frame,
        "dominance_result": dominance_result,
        "release_frames": [],
        "release_exit_timer": None,
        "spans": {},
        "active": None,
        "entry": {},
    }


def _markers_ready(entries):
    """True when every present marker is within tolerance."""
    if not entries:
        return None          # no markers present -> not a constraint
    errs = [e[1] for e in entries if e[1] is not None]
    if not errs:
        return None
    return all(e <= MARKER_TOLERANCE_M for e in errs)


def _ptb_entry_snapshot(ope, world, timer):
    """Positioning truth at the moment PTB_PREP begins.

    This is the PARITY metric: it is measured AFTER RELEASE has exited, so
    it shows whether a shorter RELEASE left any mover short of its arrival
    position.  Baseline vs new are compared on exactly this row.
    """
    entries = marker_errors(ope)
    bcerr, bcspeed = bc_error(world)
    dh = ope.dh
    bc = world.ball_carrier
    dh_err = None
    if dh is not None and bc is not None:
        ruck_y = getattr(world, "ruck_y_m", bc.y)
        dh_err = math.hypot(
            (bc.x - ope_mod.DH_BEHIND_DISTANCE_M) - dh.x,
            float(ruck_y) - dh.y,
        )
    return {
        "t": timer,
        "markers": entries,
        "bc_err": bcerr,
        "bc_speed": bcspeed,
        "dh_err": dh_err,
        "dh_ok": not dh_gate_fails(ope, world, for_exec=False),
        "ruck_x": getattr(world, "ruck_x_m", None),
    }


def exec_blockers(ope, world):
    """Mirror of PTBManager.update_exec's ready gate, as blocker tags.

    Returns the list of conditions that are FALSE this frame, so a cycle
    that outlives the PTB_EXEC watchdog can be explained exactly.
    """
    mgr = ope.ptb_manager
    dh = ope.dh
    bc = world.ball_carrier
    if dh is None:
        return ["A_dh_none"]
    if bc is None:
        return ["B_bc_none"]
    out = []
    if mgr.state != "exec":
        out.append("state=%s" % mgr.state)
    if mgr.timer < 0.15:
        out.append("timer=%.2f" % mgr.timer)
    ruck_y = getattr(world, "ruck_y_m", bc.y)
    target_x = bc.x - 1.0
    dist = math.hypot(target_x - dh.x, float(ruck_y) - dh.y)
    if dist >= 0.55:
        out.append("dist=%.2f" % dist)
    if abs(dh.y - float(ruck_y)) >= 0.55:
        out.append("lateral=%.2f" % abs(dh.y - float(ruck_y)))
    spd = math.hypot(dh.vx, dh.vy)
    if spd >= 1.1:
        out.append("speed=%.2f" % spd)
    if not (dh.x < bc.x):
        out.append("not_behind(dx=%.2f)" % (dh.x - bc.x))
    if dh.vx < -0.5:
        out.append("retreat_vx=%.2f" % dh.vx)
    return out


def observe(cur, ope, world, phase, frame, timer):
    span = cur["active"]
    if span is None or span != phase:
        span = phase
        cur["active"] = phase
        cur["spans"].setdefault(phase, {"entry_frame": frame,
                                        "entry_timer": timer,
                                        "frames": 0,
                                        "last_timer": timer})
    st = cur["spans"][phase]
    st["frames"] += 1
    st["last_timer"] = timer

    if phase == "PTB_PREP":
        if "ptb_prep_entry" not in cur:
            cur["ptb_prep_entry"] = _ptb_entry_snapshot(ope, world, timer)
        return

    if phase == "PTB_EXEC":
        _dh = ope.dh
        _bc = world.ball_carrier
        cur.setdefault("exec_frames", []).append({
            "frame": frame,
            "t": timer,
            "blockers": exec_blockers(ope, world),
            "dh_id": getattr(_dh, "player_id", None),
            "dh_x": getattr(_dh, "x", None),
            "dh_y": getattr(_dh, "y", None),
            "dh_team": getattr(_dh, "team", None),
            "bc_id": getattr(_bc, "player_id", None),
            "bc_x": getattr(_bc, "x", None),
            "bc_y": getattr(_bc, "y", None),
            "bc_team": getattr(_bc, "team", None),
            "ruck_x": getattr(world, "ruck_x_m", None),
            "mgr_state": ope.ptb_manager.state,
        })
        return

    if phase != "RELEASE":
        return

    fails = dh_gate_fails(ope, world, for_exec=False)
    entries = marker_errors(ope)
    bcerr, bcspeed = bc_error(world)
    cur["release_frames"].append({
        "frame": frame,
        "t": timer,
        "dh_ok": not fails,
        "dh_fails": fails,
        "markers": entries,
        "markers_ok": _markers_ready(entries),
        "bc_err": bcerr,
        "bc_speed": bcspeed,
        "bc_ok": (bcerr is not None and bcerr <= BC_POSITION_TOLERANCE_M)
        or (bcspeed is not None and bcspeed <= BC_SPEED_TOLERANCE_MPS),
    })


def close_cycle(cur, frame):
    cur["end_frame"] = frame
    if cur["release_frames"]:
        cur["release_exit_timer"] = cur["release_frames"][-1]["t"] + DT
    return cur


def run_rollout():
    cycles = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()
        cur = None
        dominance_result = None
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

            if phase == "TACKLE_DOMINANCE" and ope.tes_ctx is not None:
                dominance_result = getattr(ope.tes_ctx, "dominance_result", None)

            if cur is None and phase in TES_PHASES:
                cur = new_cycle(frame, dominance_result)

            if cur is not None:
                observe(cur, ope, world, phase, frame, timer)
                if phase not in CHAIN:
                    # DOMINANCE is now only ~3 frames, so sampling it
                    # inside the phase misses it.  tes_outcome persists and
                    # carries the resolved tackle_result for the cycle.
                    _out = getattr(ope, "tes_outcome", None)
                    if _out is not None:
                        _res = getattr(_out, "tackle_result", None)
                        if _res is not None:
                            cur["dominance_result"] = _res
                    cycles.append(close_cycle(cur, frame))
                    cur = None
                    dominance_result = None

        if cur is not None:
            cycles.append(close_cycle(cur, int(world.frame_count)))
    return cycles


# ---------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------

def stats(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {"n": n, "min": vals[0], "median": vals[n // 2],
            "mean": sum(vals) / n, "max": vals[-1]}


def fmt(value, nd=3):
    if value is None:
        return "-"
    try:
        return ("%." + str(nd) + "f") % float(value)
    except (TypeError, ValueError):
        return str(value)


def is_complete(cur):
    return all(cur["spans"].get(p) for p in RELEASE_TAIL)


def phase_dur(cur, phase):
    """Duration (s) of *phase* in this cycle, or None."""
    st = cur["spans"].get(phase)
    if not st:
        return None
    return st["frames"] * DT


def marker_error_of(cur, name):
    """Marker *name* error (m) at PTB_PREP entry, or None."""
    entry = cur.get("ptb_prep_entry") or {}
    for (mname, err, _tx, _ty) in entry.get("markers", []):
        if mname == name:
            return err
    return None


def component_ready_times(cur):
    """First timer at which each RELEASE component (and all together) pass."""
    t_dh = t_mark = t_bc = t_all = None
    for row in cur["release_frames"]:
        if t_dh is None and row["dh_ok"]:
            t_dh = row["t"]
        if t_mark is None and row["markers_ok"] is True:
            t_mark = row["t"]
        if t_bc is None and row["bc_ok"]:
            t_bc = row["t"]
        mark_ok = row["markers_ok"] is not False
        if (t_all is None and row["dh_ok"] and mark_ok and row["bc_ok"]):
            t_all = row["t"]
    return t_dh, t_mark, t_bc, t_all


def main():
    print("=" * 118)
    print("RELEASE CONVERGENCE BUDGET - what actually binds the RELEASE exit")
    print("=" * 118)
    print("  frames per rollout : %d   rollouts : %d   dt : %.6f"
          % (MAX_FRAMES, ROLLOUTS, DT))
    print("  tolerances         : marker %.2f m   BC pos %.2f m   BC speed %.2f m/s"
          % (MARKER_TOLERANCE_M, BC_POSITION_TOLERANCE_M,
             BC_SPEED_TOLERANCE_MPS))
    print("  marker writer speed: %.2f m/s   (MARKER_MOVE_SPEED_MPS)"
          % MARKER_SPEED_MPS)
    print()

    all_cycles = []
    for i in range(ROLLOUTS):
        all_cycles.extend(run_rollout())
        print("  rollout %d: %d cycles recorded" % (i + 1, len(all_cycles)))
    print()

    complete = [c for c in all_cycles if is_complete(c)]
    print("  cycles total       : %d" % len(all_cycles))
    print("  cycles to PTB      : %d" % len(complete))
    print("  dominance mix      : %s"
          % dict(Counter(str(c["dominance_result"]) for c in complete)))
    print()

    if not complete:
        print("RESULT: no complete cycles - nothing to measure.")
        return 2

    rows = [(c,) + component_ready_times(c) for c in complete]

    print("-" * 118)
    print("PER-CYCLE RELEASE CONVERGENCE (seconds)")
    print("-" * 118)
    print("   #  dom        rel_exit  t_dh    t_markers  t_bc    t_all   dead_clock")
    print("-" * 118)
    for idx, (c, t_dh, t_mark, t_bc, t_all) in enumerate(rows):
        exit_t = c["release_exit_timer"]
        dead = None
        if t_all is not None and exit_t is not None:
            dead = max(0.0, exit_t - t_all)
        print("%4d  %-9s  %8s  %6s  %9s  %6s  %6s  %10s"
              % (idx, str(c["dominance_result"]), fmt(exit_t),
                 fmt(t_dh), fmt(t_mark), fmt(t_bc), fmt(t_all), fmt(dead)))
    print()

    exit_stats = stats([c["release_exit_timer"] for c in complete])
    t_dh_stats = stats([r[1] for r in rows])
    t_mark_stats = stats([r[2] for r in rows])
    t_bc_stats = stats([r[3] for r in rows])
    t_all_stats = stats([r[4] for r in rows])
    dead_stats = stats([
        (c["release_exit_timer"] - t_all) if (t_all is not None
                                              and c["release_exit_timer"] is not None)
        else None
        for (c, _, _, _, t_all) in rows
    ])

    print("-" * 118)
    print("LEDGER (seconds)")
    print("-" * 118)
    print("  %-34s %4s %8s %8s %8s %8s"
          % ("component", "n", "min", "median", "mean", "max"))
    for label, st in (
        ("RELEASE exit (actual)", exit_stats),
        ("DH predicate first pass", t_dh_stats),
        ("BOTH markers in tolerance", t_mark_stats),
        ("BC recovered", t_bc_stats),
        ("ALL components ready (t_all)", t_all_stats),
        ("DEAD CLOCK (exit - t_all)", dead_stats),
    ):
        if st is None:
            print("  %-34s %4d %8s %8s %8s %8s" % (label, 0, "-", "-", "-", "-"))
            continue
        print("  %-34s %4d %8s %8s %8s %8s"
              % (label, st["n"], fmt(st["min"]), fmt(st["median"]),
                 fmt(st["mean"]), fmt(st["max"])))
    print()

    print("-" * 118)
    print("WHICH COMPONENT BINDS t_all")
    print("-" * 118)
    binding = Counter()
    for (c, t_dh, t_mark, t_bc, t_all) in rows:
        if t_all is None:
            binding["none"] += 1
            continue
        cands = {"dh": t_dh, "markers": t_mark, "bc": t_bc}
        cands = {k: v for k, v in cands.items() if v is not None}
        if not cands:
            binding["unknown"] += 1
            continue
        for k, v in cands.items():
            if abs(v - t_all) < 1e-9:
                binding[k] += 1
    for key, count in binding.most_common():
        print("  %-10s %d" % (key, count))
    print()

    print("-" * 118)
    print("MARKER ENTRY GAP AT RELEASE ENTRY (travel required / speed = floor)")
    print("-" * 118)
    entry_gaps = []
    for (c, _, _, _, _) in rows:
        first = c["release_frames"][0] if c["release_frames"] else None
        if not first:
            continue
        for (name, err, _tx, _ty) in first["markers"]:
            if err is not None:
                entry_gaps.append((name, err))
    for name in sorted({n for n, _ in entry_gaps}):
        errs = [e for n, e in entry_gaps if n == name]
        st = stats(errs)
        if st is None:
            continue
        print("  %-10s n=%2d  min=%s  median=%s  max=%s   -> min travel time %s s"
              % (name, st["n"], fmt(st["min"]), fmt(st["median"]), fmt(st["max"]),
                 fmt(st["max"] / MARKER_SPEED_MPS if MARKER_SPEED_MPS else None)))
    print()

    print("-" * 118)
    print("PHASE LEDGER (seconds) - CURRENT CODE")
    print("-" * 118)
    print("  %-22s %4s %8s %8s %8s %8s"
          % ("phase", "n", "min", "median", "mean", "max"))
    chain_vals = []
    for ph in ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
               "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB"):
        st = stats([phase_dur(c, ph) for c in complete])
        if st is None:
            print("  %-22s %4d %8s %8s %8s %8s" % (ph, 0, "-", "-", "-", "-"))
            continue
        print("  %-22s %4d %8s %8s %8s %8s"
              % (ph, st["n"], fmt(st["min"]), fmt(st["median"]),
                 fmt(st["mean"]), fmt(st["max"])))
    for c in complete:
        parts = [phase_dur(c, p) for p in
                 ("TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC")]
        if all(p is not None for p in parts):
            chain_vals.append(sum(parts))
    st_chain = stats(chain_vals)
    if st_chain is not None:
        print()
        print("  DRIVE_END -> PTB_ENTER (DOMINANCE + RELEASE + PREP + EXEC)")
        print("    n=%d  min=%s  median=%s  mean=%s  max=%s"
              % (st_chain["n"], fmt(st_chain["min"]), fmt(st_chain["median"]),
                 fmt(st_chain["mean"]), fmt(st_chain["max"])))
    print()

    print("-" * 118)
    print("PTB_EXEC GATE (why the phase ran long, if it did)")
    print("-" * 118)
    exec_lens = [len(c.get("exec_frames") or []) for c in complete]
    st_exec = stats(exec_lens)
    if st_exec is not None:
        print("  exec frames              : min=%d median=%d mean=%.1f max=%d"
              % (st_exec["min"], st_exec["median"], st_exec["mean"],
                 st_exec["max"]))
    long_cycles = [c for c in complete
                   if len(c.get("exec_frames") or []) > 9]
    print("  cycles past the 9-frame timer floor : %d / %d"
          % (len(long_cycles), len(complete)))
    for c in long_cycles[:6]:
        frames = c.get("exec_frames") or []
        last = frames[-1]
        print("    start_frame=%s  frames=%d  last_blockers=%s"
              % (c.get("start_frame"), len(frames), last["blockers"]))
        print("      DH  #%s team=%s  (%.1f, %.1f)"
              % (last.get("dh_id"), last.get("dh_team"),
                 last.get("dh_x") or 0.0, last.get("dh_y") or 0.0))
        print("      BC  #%s team=%s  (%.1f, %.1f)   ruck_x=%s  mgr=%s"
              % (last.get("bc_id"), last.get("bc_team"),
                 last.get("bc_x") or 0.0, last.get("bc_y") or 0.0,
                 last.get("ruck_x"), last.get("mgr_state")))
    print()

    print("-" * 118)
    print("POSITIONING PARITY AT PTB_PREP ENTRY (metres, lower = more arrived)")
    print("-" * 118)
    for label, vals in (
        ("MARKER_A error", [marker_error_of(c, "MARKER_A") for c in complete]),
        ("MARKER_B error", [marker_error_of(c, "MARKER_B") for c in complete]),
        ("DH error to bc.x-1", [(c.get("ptb_prep_entry") or {}).get("dh_err")
                                for c in complete]),
        ("BC error to target", [(c.get("ptb_prep_entry") or {}).get("bc_err")
                                for c in complete]),
    ):
        st = stats(vals)
        if st is None:
            print("  %-22s %4d %8s %8s %8s %8s" % (label, 0, "-", "-", "-", "-"))
            continue
        print("  %-22s %4d %8s %8s %8s %8s"
              % (label, st["n"], fmt(st["min"]), fmt(st["median"]),
                 fmt(st["mean"]), fmt(st["max"])))
    dh_ok_n = sum(1 for c in complete
                  if (c.get("ptb_prep_entry") or {}).get("dh_ok"))
    print("  DH gate satisfied at PTB_PREP entry : %d / %d"
          % (dh_ok_n, len(complete)))
    print()

    print("-" * 118)
    print("VERDICT")
    print("-" * 118)
    if exit_stats and t_all_stats:
        print("  RELEASE actually runs       : mean %s s"
              % fmt(exit_stats["mean"]))
        print("  time until ALL RELEASE-owned movers are converged : mean %s s"
              % fmt(t_all_stats["mean"]))
        print("  DEAD CLOCK a convergence-first exit removes       : mean %s s"
              % (fmt(dead_stats["mean"]) if dead_stats else "-"))
    if t_mark_stats:
        print("  markers are the binding term in %d/%d cycles"
              % (binding.get("markers", 0), len(rows)))
    print()
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
