"""tmp/probe_stage5_corridor.py — Stage 5 POST-TES REINTEGRATION CORRIDOR probe.

WHAT IT MEASURES
----------------
Five questions about the corridor between TES ownership and line membership,
read through ``game/defence/defence_lvl_1/post_tes_corridor_authority.py``:

  Q1 CORRIDOR TRANSIT   For every body that leaves TES ownership, how many
                        SIMULATION FRAMES pass before it is counted in the
                        line population for the first time AFTER that leave?
                        (Stage 4B's number was measured in ``world.frame_count``
                        units, which that probe's own bootstrap advanced twice
                        per simulated frame -- so it is not a frame count.
                        This probe counts its own ticks.)

  Q2 BARRIER CENSUS     Which declared barrier held each body out of the line
                        on every frame of the corridor, and which declared
                        HOLD (if any) owned the motionless ones.

  Q3 MOVEMENT           Per-frame displacement of every RELEASED body: how
                        many corridor body-frames were motionless, and what
                        target error remained on them.

  Q4 STUCK-NOT-FROZEN   The PROJECT'S OWN predicate (``freeze_audit``): a
                        body with NO declared hold and no motion for
                        ``STUCK_WINDOW_FRAMES`` consecutive frames while a
                        live target waits, split into STUCK_OWNED (a writer
                        stamped the target and the frame did not integrate
                        it) and STUCK_UNOWNED (nobody owns it).

  Q5 LATENT POPULATIONS Bodies that sit in RELEASED without ever having been
                        TES_OWNED -- the permanent exclusions (marker / DH /
                        chase / fullback roles).  Declared or defect, named.

METHOD
------
Read-only.  Boots the real engine headless (same bootstrap as
probe_stage4b_exit_ownership.py), steps the match, and records one
observation per OPEN_PLAY frame.  It installs ``world._target_write_log`` so
``stamp_target_write`` records every write; the list is CLEARED at the start
of each frame so memory is bounded to one frame of records.

NOTHING in production is modified by this probe.

Run:  python -u tmp/probe_stage5_corridor.py
"""

import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
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

from game.defence.defence_lvl_1 import post_tes_corridor_authority as pta
from game.defence.defence_lvl_1 import exit_transition_authority as eta

DT = 1.0 / 60.0
# Frame budget and rollout count.  Overridable from the environment so a
# quick smoke run costs seconds instead of minutes:
#     S5_ROLLOUTS=1 S5_FRAMES=1200   ->  ~30 s
MAX_FRAMES = int(os.environ.get("S5_FRAMES", "3600"))
ROLLOUTS = int(os.environ.get("S5_ROLLOUTS", "3"))

# A run longer than this (simulation frames) is closed as "never rejoined".
# 240 frames = 4.0 s, past every measured exit era max (2.65 s) with a full
# second of open play behind it.
MAX_RUN_FRAMES = 240


# ─────────────────────────────────────────────────────────────────────
# BOOTSTRAP (mirrors probe_stage4b_exit_ownership.py)
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
    world._target_write_log = []
    return world, ball, open_play, kickoff_engine, collision_system


def writes_this_frame(world):
    """{player_id: [writer, ...]} from the engine's own write log, this frame."""
    store = getattr(world, "_target_write_log", None)
    if not store:
        return {}
    out = defaultdict(list)
    for rec in store:
        pid = rec.get("player_id")
        if pid is None:
            continue
        writer = rec.get("writer")
        if writer:
            out[pid].append(writer)
    return out


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _role_name(d):
    role = getattr(d, "defender_role", None)
    if role is None:
        return "None"
    return getattr(role, "name", None) or str(role)


def bucket(n: int) -> str:
    """Duration bucket label for a frame count."""
    if n <= 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 5:
        return "2-5"
    if n <= 20:
        return "6-20"
    if n <= 60:
        return "21-60"
    if n <= 120:
        return "61-120"
    return "121+"


def new_run(tick, frame, d, world, phase):
    """Open a reintegration run for a body that just left TES ownership."""
    return {
        "pid": getattr(d, "player_id", None),
        "class": eta.body_class(d, world),
        "role": _role_name(d),
        "start_tick": tick,
        "start_frame": frame,
        "start_phase": phase,
        "barriers": Counter(),
        "holds": Counter(),
        "stuck": Counter(),
        "stuck_by_phase": Counter(),
        "writers": Counter(),
        "motionless": 0,
        "frames": 0,
    }


# ─────────────────────────────────────────────────────────────────────
# ONE ROLLOUT
# ─────────────────────────────────────────────────────────────────────

def run_rollout(out):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()

        tick = 0                    # TRUE simulation frames this probe stepped
        prev_node = {}              # id(d) -> corridor node last frame
        prev_pos = {}               # id(d) -> (x, y) last frame
        stuck_streak = {}           # id(d) -> consecutive soft-stuck frames
        runs = {}                   # id(d) -> open run record

        for _ in range(MAX_FRAMES):
            if world.phase == GamePhase.KICKOFF:
                world.frame_count += 1
                ball.update(DT)
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                world.frame_count += 1
                ball.update(DT)
                if world.ball_carrier is not None:
                    ope.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                world.frame_count += 1
                ball.update(DT)
                continue

            # OPEN_PLAY tick: ope.update() owns the frame_count increment.
            world._target_write_log.clear()
            ope.update(DT)
            tick += 1
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = str(ope._phase)
            frame = int(world.frame_count)
            writes = writes_this_frame(world)
            world._ope_phase_label = phase

            _observe(out, world, tick, frame, phase, writes,
                     prev_node, prev_pos, stuck_streak, runs)

        for key, run in list(runs.items()):
            _close_run(out, run, None, tick, "rollout_end")
            del runs[key]


def _observe(out, world, tick, frame, phase, writes,
             prev_node, prev_pos, stuck_streak, runs):
    """Classify every defender this frame and advance the run machine."""
    for d in (getattr(world, "defending_team", None) or []):
        if d is None:
            continue
        key = id(d)
        pid = getattr(d, "player_id", None)
        node = pta.corridor_node(d, world)
        barrier = pta.reintegration_barrier(d, world)
        old_pos = prev_pos.get(key)
        x = _f(getattr(d, "x", 0.0))
        y = _f(getattr(d, "y", 0.0))
        moved = None
        if old_pos is not None:
            moved = math.hypot(x - old_pos[0], y - old_pos[1])

        # ── the project's stuck window, tracked per body ──
        soft = pta.is_soft_stuck(d, world, phase)
        streak = (stuck_streak.get(key, 0) + 1) if soft else 0
        stuck_streak[key] = streak
        verdict = pta.is_stuck_not_frozen(
            d, world, phase=phase, stuck_streak=streak,
            writers_this_frame=writes.get(pid, ()),
        )

        run = runs.get(key)
        if run is not None:
            if node == pta.LINE_MEMBER:
                _close_run(out, run, (tick - run["start_tick"]), tick,
                           "readmitted")
                del runs[key]
            elif node == pta.TES_OWNED:
                _close_run(out, run, None, tick, "re-engaged")
                del runs[key]
            else:
                _accumulate_corridor(out, run, world, d, phase, barrier,
                                     writes.get(pid, ()), moved, verdict)
                if run["frames"] > MAX_RUN_FRAMES:
                    _close_run(out, run, None, tick, _close_reason_for(run))
                    del runs[key]
        else:
            prev = prev_node.get(key)
            if prev == pta.TES_OWNED and node != pta.TES_OWNED:
                runs[key] = new_run(tick, frame, d, world, phase)
            elif node == pta.RELEASED and prev is None:
                # First observation of this body and it is already outside
                # the line: a LATENT population member.
                out["latent_frames"][(_role_name(d), barrier or "-")] += 1
                if moved is not None and moved <= pta.POSITION_EPS_M:
                    out["latent_motionless"] += 1
                if verdict is not None:
                    out["latent_stuck"] += 1

        prev_node[key] = node
        prev_pos[key] = (x, y)


def _accumulate_corridor(out, run, world, d, phase, barrier, writers, moved,
                         verdict):
    """Record one RELEASED frame for a body already in the corridor."""
    run["frames"] += 1
    run["barriers"][barrier or "NONE(window)"] += 1
    hold = pta.declared_hold(d, world, phase)
    run["holds"][hold or "NONE(owned-by-motion)"] += 1
    out["corridor_frames"] += 1
    out["barrier_by_phase"][phase][barrier or "NONE(window)"] += 1
    out["hold_by_phase"][phase][hold or "NONE(owned-by-motion)"] += 1
    for w in writers:
        run["writers"][w] += 1
        out["corridor_writers"][w] += 1

    if moved is not None:
        out["corridor_dy"].append(moved)
        if moved <= pta.POSITION_EPS_M:
            out["corridor_motionless"] += 1
            run["motionless"] += 1

    if verdict is not None:
        err = pta.target_error_m(d)
        run["stuck"][verdict] += 1
        run["stuck_by_phase"][phase] += 1
        out["stuck_total"] += 1
        out["stuck_by_verdict"][verdict] += 1
        out["stuck_by_phase"][phase] += 1
        out["stuck_by_barrier"][barrier or "NONE(window)"] += 1
        out["stuck_by_class"][eta.body_class(d, world)] += 1
        if err is not None:
            out["stuck_target_err"].append(err)


def _close_reason_for(run):
    """Name why an over-long run is being closed.

    A run that carried ONE declared barrier for its whole life is not a
    failure: it is a declared PERMANENT population member.  The dynamic
    fullback is exactly that -- it owns its own backfield anchor writer and
    never rejoins the line -- so closing it as "never rejoined" would
    misreport a designed exclusion as a defect.
    """
    barriers = run["barriers"]
    if len(barriers) == 1 and next(iter(barriers)) == pta.BARRIER_FULLBACK:
        return "declared_permanent_fullback"
    return "never_rejoined"


def _close_run(out, run, latency, tick, reason):
    """Flush a finished run into the aggregate stats."""
    run["end_tick"] = tick
    run["end_reason"] = reason
    if reason == "readmitted" and latency is not None:
        out["latency_frames"].append(latency)
        out["latency_by_class"][run["class"]].append(latency)
        out["latency_bucket"][bucket(latency)] += 1
        out["readmitted"] += 1
    else:
        out["not_readmitted"] += 1
        out["not_readmitted_reason"][reason] += 1
        out["not_readmitted_class"][run["class"]] += 1
    out["releases"] += 1
    out["released_by_class"][run["class"]] += 1
    out["run_barriers"][
        tuple(sorted(run["barriers"].items(), key=lambda kv: (-kv[1], str(kv[0]))))
    ] += 1
    out["run_records"].append({
        "pid": run["pid"],
        "class": run["class"],
        "role": run["role"],
        "start_tick": run["start_tick"],
        "start_phase": run["start_phase"],
        "frames": run["frames"],
        "latency": latency,
        "reason": reason,
        "motionless": run["motionless"],
        "stuck": dict(run["stuck"]),
        "stuck_by_phase": dict(run["stuck_by_phase"]),
        "barriers": dict(run["barriers"].most_common(6)),
        "holds": dict(run["holds"].most_common(6)),
        "writers": dict(run["writers"].most_common(6)),
    })


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def stats(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {"n": n, "min": vals[0], "median": vals[n // 2],
            "mean": sum(vals) / n, "max": vals[-1]}


def fmt(v, nd=2):
    if v is None:
        return "-"
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)


def report_transit(out):
    print("=" * 100)
    print("Q1  CORRIDOR TRANSIT — TES_OWNED -> RELEASED -> LINE_MEMBER")
    print("=" * 100)
    print("  units: SIMULATION FRAMES (probe ticks), not world.frame_count")
    print("  bodies that left TES ownership : %d" % out["releases"])
    print("  by class at release            : %s"
          % dict(out["released_by_class"].most_common()))
    print("  re-admitted to the line        : %d" % out["readmitted"])
    print("  NOT re-admitted                : %d  %s"
          % (out["not_readmitted"],
             dict(out["not_readmitted_reason"].most_common())))
    if out["not_readmitted_class"]:
        print("    not-readmitted by class      : %s"
              % dict(out["not_readmitted_class"].most_common()))
    st = stats(out["latency_frames"])
    if st:
        print()
        print("  leave-TES -> first line membership AFTER the leave:")
        print("      n=%d min=%s median=%s mean=%s max=%s frames"
              % (st["n"], fmt(st["min"], 0), fmt(st["median"], 0),
                 fmt(st["mean"]), fmt(st["max"], 0)))
        print("      duration buckets: %s"
              % dict(sorted(out["latency_bucket"].items())))
    print()
    print("  PER CLASS (n / mean / max frames):")
    for cls, vals in sorted(out["latency_by_class"].items()):
        s = stats(vals)
        if s:
            print("      %-18s n=%-4d mean=%-7s max=%s"
                  % (cls, s["n"], fmt(s["mean"]), fmt(s["max"], 0)))
    print()


def report_barriers(out):
    print("=" * 100)
    print("Q2  BARRIER CENSUS — what held each body out of the line")
    print("=" * 100)
    print("  corridor body-frames (node == RELEASED): %d" % out["corridor_frames"])
    print()
    print("  PER SUB-PHASE — admission barrier, then declared hold:")
    for phase in sorted(out["barrier_by_phase"]):
        counts = out["barrier_by_phase"][phase]
        holds = out["hold_by_phase"][phase]
        total = sum(counts.values())
        print("  %-20s n=%-6d" % (phase, total))
        print("      barrier : %s" % pta.format_barrier_report(counts))
        print("      hold    : %s"
              % ", ".join("%s=%d" % (k, v) for k, v in
                          sorted(holds.items(), key=lambda kv: (-kv[1], str(kv[0])))))
    print()
    print("  barrier held per COMPLETED run (top patterns):")
    for pattern, n in out["run_barriers"].most_common(8):
        desc = " , ".join("%s x%d" % (k, v) for k, v in pattern)
        print("      n=%-4d %s" % (n, desc[:150]))
    print()


def report_movement(out):
    print("=" * 100)
    print("Q3  MOVEMENT CONTINUITY during the corridor")
    print("=" * 100)
    st = stats(out["corridor_dy"])
    if st:
        print("  |dpos| per RELEASED body-frame: n=%d min=%s median=%s "
              "mean=%s max=%s m"
              % (st["n"], fmt(st["min"], 4), fmt(st["median"], 4),
                 fmt(st["mean"], 4), fmt(st["max"], 4)))
    print("  motionless RELEASED body-frames (|dpos| <= %.3f m): %d  (%.2f%% of %d)"
          % (pta.POSITION_EPS_M, out["corridor_motionless"],
             (100.0 * out["corridor_motionless"] / out["corridor_frames"])
             if out["corridor_frames"] else 0.0,
             out["corridor_frames"]))
    print()
    print("  writers seen on RELEASED bodies (top 12):")
    for w, n in out["corridor_writers"].most_common(12):
        print("      %-24s %6d" % (w, n))
    print()


def report_stuck(out):
    print("=" * 100)
    print("Q4  STUCK-NOT-FROZEN — the freeze_audit contract")
    print("=" * 100)
    print("  window: %d consecutive frames, no declared hold, live target"
          % pta.STUCK_WINDOW_FRAMES)
    print("  stuck body-frames            : %d" % out["stuck_total"])
    if out["stuck_total"]:
        print("  by verdict                   : %s"
              % dict(out["stuck_by_verdict"].most_common()))
        print("  by class                     : %s"
              % dict(out["stuck_by_class"].most_common()))
        print("  by barrier                   : %s"
              % dict(out["stuck_by_barrier"].most_common()))
        print("  by sub-phase                 : %s"
              % dict(out["stuck_by_phase"].most_common()))
        err = stats(out["stuck_target_err"])
        if err:
            print("  target error on those frames : min=%s median=%s mean=%s max=%s m"
                  % (fmt(err["min"], 3), fmt(err["median"], 3),
                     fmt(err["mean"], 3), fmt(err["max"], 3)))
    print()


def report_latent(out):
    print("=" * 100)
    print("Q5  LATENT POPULATIONS — RELEASED without ever being TES_OWNED")
    print("=" * 100)
    print("  RELEASED body-frames with no corridor run (role, barrier):")
    for key, n in out["latent_frames"].most_common(14):
        print("      %-24s %-24s %6d" % (key[0], str(key[1]), n))
    print("  of which motionless : %d" % out["latent_motionless"])
    print("  of which stuck      : %d" % out["latent_stuck"])
    print()


def report_tail(out):
    print("=" * 100)
    print("Q4b  THE TAIL — slowest / never-re-admitted runs")
    print("=" * 100)
    records = sorted(out["run_records"],
                     key=lambda r: (-(r["latency"] or 10 ** 6), -r["frames"]))
    shown = 0
    for r in records:
        if r["reason"] == "readmitted" and r["latency"] is not None \
                and r["latency"] <= 5:
            continue
        shown += 1
        if shown > 12:
            break
        print("  pid=%-6s class=%-16s role=%-16s start_tick=%-6d phase=%s"
              % (r["pid"], r["class"], r["role"], r["start_tick"],
                 r["start_phase"]))
        print("      latency=%s frames=%d reason=%s motionless_frames=%d"
              % (fmt(r["latency"], 0), r["frames"], r["reason"],
                 r["motionless"]))
        print("      barriers : %s" % r["barriers"])
        print("      holds    : %s" % r["holds"])
        print("      writers  : %s" % r["writers"])
        if r["stuck"]:
            print("      stuck    : %s  by_phase=%s"
                  % (r["stuck"], r["stuck_by_phase"]))
        print()


def main():
    out = {
        "releases": 0,
        "readmitted": 0,
        "not_readmitted": 0,
        "not_readmitted_reason": Counter(),
        "not_readmitted_class": Counter(),
        "released_by_class": Counter(),
        "latency_frames": [],
        "latency_by_class": defaultdict(list),
        "latency_bucket": Counter(),
        "run_barriers": Counter(),
        "run_records": [],
        "corridor_frames": 0,
        "corridor_motionless": 0,
        "corridor_dy": [],
        "corridor_writers": Counter(),
        "barrier_by_phase": defaultdict(Counter),
        "hold_by_phase": defaultdict(Counter),
        "stuck_total": 0,
        "stuck_by_verdict": Counter(),
        "stuck_by_phase": Counter(),
        "stuck_by_barrier": Counter(),
        "stuck_by_class": Counter(),
        "stuck_target_err": [],
        "latent_frames": Counter(),
        "latent_motionless": 0,
        "latent_stuck": 0,
    }
    for i in range(ROLLOUTS):
        run_rollout(out)
        print("  rollout %d/%d done — releases=%d readmitted=%d"
              % (i + 1, ROLLOUTS, out["releases"], out["readmitted"]))

    print()
    print("=" * 100)
    print("STAGE 5 — POST-TES REINTEGRATION CORRIDOR")
    print("=" * 100)
    print("  rollouts=%d  frames/rollout=%d  dt=%.6f  max run=%d frames"
          % (ROLLOUTS, MAX_FRAMES, DT, MAX_RUN_FRAMES))
    print("  corridor nodes: %s" % (", ".join(pta.CORRIDOR_NODES),))
    print("  stuck window  : %d frames (freeze_audit contract)"
          % pta.STUCK_WINDOW_FRAMES)
    print()
    report_transit(out)
    report_barriers(out)
    report_movement(out)
    report_stuck(out)
    report_latent(out)
    report_tail(out)
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
