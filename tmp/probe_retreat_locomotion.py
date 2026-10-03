"""READ-ONLY diagnostic probe: WHERE does the RETREAT locomotion speed collapse?

CONTRACT UNDER TEST
-------------------
A retreat-obligated defender (``is_retreat_obligated``, i.e. live role
DEF_RETREAT_10 and not exempt) must physically close on the retreat line
(``defensive_line_anchor_m(world)``) so the engine's own arrival test fires:

    enforce_retreat_obligation -> if d.x >= anchor: role = DEF_LINE_READY

Observed defect: obligated bodies hold a VALID retreat target (target_x > x)
while carrying zero / near-zero velocity, so the deficit never closes and the
obligation never discharges.

WHAT THIS MEASURES (per obligated body-frame, at END OF FRAME)
--------------------------------------------------------------
  geometry  : x, target_x, deficit (target_x - x), ruck_x, anchor
  command   : move_speed, movement_state
  motion    : vx, vy, speed
  gates     : frozen, freeze_timer
  seed state: depth_role, _depth_seeded, role_depth_target_x
  writer    : target_source, _mmdl_moved

CANDIDATE COLLAPSE CAUSES tallied (and cross-tabbed over the STALL frames):
  A. move_speed <= 0  -> Player._accelerate_toward_target hard-returns
                         (vx=vy=0) whenever a defensive ceiling is stamped.
  B. frozen           -> skipped by every writer AND by the integrator.
  C. freeze_timer > 0 -> retreat writer's _is_frozen() pins the body to its
                         own position and excludes it from both lists.
  D. _mmdl_moved      -> engine skips Player.update(dt) this frame.
  E. at_target        -> |target_x - x| <= 0.05 (target IS the body).
  F. no_seed          -> depth_role missing / not seeded (seed_hold's own
                         staleness definition).
  G. PTB hard-stop    -> engine zeroes vx/vy for DEF_RETREAT_10 AFTER the
                         phase handler integrated it.

STALL signature: deficit > 0.2 m AND speed < 0.01 m/s.

LEVERS (A/B; no production file is edited by this script)
---------------------------------------------------------
RLM_LEVER_SEED_HOLD_EXEMPT=1 : monkeypatch seed_hold.is_seed_stale so an
                               obligated body is never classified stale.
RLM_LEVER_SEED_HOLD_OFF=1    : monkeypatch seed_hold.apply_seed_hold to a
                               no-op.
Both default OFF; with neither set this probe is a pure observer.
"""

import contextlib
import io
import os
import statistics
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

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
from game.defence.defence_lvl_1 import retreat_obligation as ro
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)

DT = 1.0 / 60.0
FRAMES = int(os.environ.get("RLM_PROBE_FRAMES", "1800"))
ROLLOUTS = int(os.environ.get("RLM_PROBE_ROLLOUTS", "2"))

_STALL_DEFICIT_M = 0.2
_STALL_SPEED_MPS = 0.01
_AT_TARGET_M = 0.05

_PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")


def _apply_levers():
    """Install the A/B levers (default: pure observer)."""
    if os.environ.get("RLM_LEVER_SEED_HOLD_EXEMPT", "") == "1":
        from game.defence.defence_lvl_1 import seed_hold as sh
        _orig = sh.is_seed_stale

        def _exempt(d, world):
            if ro.is_retreat_obligated(d, world):
                return False
            return _orig(d, world)

        sh.is_seed_stale = _exempt
        print("  LEVER: seed_hold.is_seed_stale -> obligated bodies exempt")
    if os.environ.get("RLM_LEVER_SEED_HOLD_OFF", "") == "1":
        from game.defence.defence_lvl_1 import seed_hold as sh
        sh.apply_seed_hold = lambda world, defenders, pre_pos=None: []
        print("  LEVER: seed_hold.apply_seed_hold -> NO-OP")


def boot_world(seed):
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world, seed=seed)
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


def _blank_tally():
    return {
        "body_frames": 0,
        "stall_frames": 0,
        "move_speed_zero": 0,
        "frozen": 0,
        "freeze_timer": 0,
        "mmdl_moved": 0,
        "at_target": 0,
        "no_seed": 0,
        "stall_move_speed_zero": 0,
        "stall_frozen": 0,
        "stall_freeze_timer": 0,
        "stall_mmdl_moved": 0,
        "stall_at_target": 0,
        "stall_no_seed": 0,
        "stall_ptb_phase": 0,
        "stall_ptb_engine_stop": 0,
        "stall_seed_hold_source": 0,
        "stall_retreat_source": 0,
        "deficits": [],
        "sources": {},
        "phases": {},
        "soft_frames": 0,
        "soft_by_phase": {},
        "eff_speed_by_phase": {},
        "commanded_by_phase": {},
    }
def _tally_frame(t, world, ope, d, phase, eff_speed):
    """Accumulate one obligated body-frame (END OF FRAME state).

    ``eff_speed`` is the GROUND-TRUTH closure rate for this frame, measured
    from the per-frame displacement (``|x - prev_x| / dt``) instead of from
    the velocity field: the PTB-era hard-stop zeroes ``vx``/``vy`` AFTER the
    phase handler integrated the body, so the velocity field alone cannot
    show whether the body actually closed on its target.
    """
    x = float(getattr(d, "x", 0.0) or 0.0)
    tx = float(getattr(d, "target_x", 0.0) or 0.0)
    vx = float(getattr(d, "vx", 0.0) or 0.0)
    vy = float(getattr(d, "vy", 0.0) or 0.0)
    speed = (vx * vx + vy * vy) ** 0.5
    deficit = tx - x
    move_speed = float(getattr(d, "move_speed", 0.0) or 0.0)
    frozen = bool(getattr(d, "frozen", False))
    ftimer = float(getattr(d, "freeze_timer", 0.0) or 0.0)
    mmdl = bool(getattr(d, "_mmdl_moved", False))
    depth_role = getattr(d, "depth_role", None)
    seeded = bool(getattr(d, "_depth_seeded", False))
    rdtx = getattr(d, "role_depth_target_x", None)
    no_seed = (depth_role is None) or (not seeded) or (rdtx is None)
    src = str(getattr(d, "target_source", "") or "")
    at_target = abs(deficit) <= _AT_TARGET_M

    t["body_frames"] += 1
    t["move_speed_zero"] += int(move_speed <= 0.0)
    t["frozen"] += int(frozen)
    t["freeze_timer"] += int(ftimer > 0.0)
    t["mmdl_moved"] += int(mmdl)
    t["at_target"] += int(at_target)
    t["no_seed"] += int(no_seed)
    t["sources"][src] = t["sources"].get(src, 0) + 1
    t["phases"][phase] = t["phases"].get(phase, 0) + 1
    if eff_speed is not None:
        t["eff_speed_by_phase"].setdefault(phase, []).append(eff_speed)
        t["commanded_by_phase"].setdefault(phase, []).append(move_speed)
        if (deficit > _STALL_DEFICIT_M and move_speed > 0.1
                and eff_speed < 0.5 * move_speed):
            t["soft_frames"] += 1
            t["soft_by_phase"][phase] = t["soft_by_phase"].get(phase, 0) + 1

    if deficit > _STALL_DEFICIT_M and speed < _STALL_SPEED_MPS:
        t["stall_frames"] += 1
        t["deficits"].append(deficit)
        t["stall_move_speed_zero"] += int(move_speed <= 0.0)
        t["stall_frozen"] += int(frozen)
        t["stall_freeze_timer"] += int(ftimer > 0.0)
        t["stall_mmdl_moved"] += int(mmdl)
        t["stall_at_target"] += int(at_target)
        t["stall_no_seed"] += int(no_seed)
        t["stall_seed_hold_source"] += int(src == "seed_hold")
        t["stall_retreat_source"] += int(src.startswith("retreat"))
        in_ptb = phase in _PTB_PHASES
        t["stall_ptb_phase"] += int(in_ptb)
        t["stall_ptb_engine_stop"] += int(in_ptb and vx == 0.0 and vy == 0.0)


def _trace_sample(world, d, phase, pid):
    """One diagnostic trace row for a stall frame."""
    x = float(getattr(d, "x", 0.0) or 0.0)
    tx = float(getattr(d, "target_x", 0.0) or 0.0)
    return {
        "frame": int(world.frame_count),
        "engine_phase": phase,
        "pid": pid,
        "x": round(x, 2),
        "anchor": round(float(_anchor_m(world)), 2),
        "ruck_x": round(float(getattr(world, "ruck_x_m", 0.0) or 0.0), 2),
        "target_x": round(tx, 2),
        "deficit": round(tx - x, 2),
        "vx": round(float(getattr(d, "vx", 0.0) or 0.0), 3),
        "vy": round(float(getattr(d, "vy", 0.0) or 0.0), 3),
        "move_speed": round(float(getattr(d, "move_speed", 0.0) or 0.0), 3),
        "state": str(getattr(getattr(d, "movement_state", None), "name", "")),
        "src": str(getattr(d, "target_source", "") or ""),
        "frozen": bool(getattr(d, "frozen", False)),
        "ftimer": round(float(getattr(d, "freeze_timer", 0.0) or 0.0), 2),
        "mmdl_moved": bool(getattr(d, "_mmdl_moved", False)),
        "depth_role": getattr(d, "depth_role", None),
        "seeded": bool(getattr(d, "_depth_seeded", False)),
        "rdtx": getattr(d, "role_depth_target_x", None),
    }


def run_rollout(seed, trace_limit=14):
    """Run one rollout; return the tally plus the first stall traces."""
    buf = io.StringIO()
    tally = _blank_tally()
    trace = []
    runs = {}
    current = {}
    prev_x = {}
    prev_live = set()
    run_phase = {}
    cur_phase = {}
    # ── LONG-RUN DEFICIT RING (per body) ──
    # The deficit trajectory of a run that lasts many frames is the ONLY
    # evidence that separates "the body closes but the anchor is re-armed by
    # the next tackle" (legitimate multi-cycle obligation) from "the body is
    # stuck short of the line" (a genuine residual stall).  A small ring
    # buffer per body holds the most recent samples and is dumped only when a
    # run closes after many frames.
    from collections import deque as _deque
    ring = {}
    long_runs = []
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world(seed)
        for _ in range(FRAMES):
            world.frame_count += 1
            ball.update(DT)
            phase = world.phase
            if phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    ope.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if phase != GamePhase.OPEN_PLAY:
                continue

            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            engine_phase = str(getattr(ope, "_phase", ""))
            team = list(getattr(world, "defending_team", None) or ())
            _cur_live = set()
            for d in team:
                pid = getattr(d, "player_id", "?")
                if not ro.is_retreat_obligated(d, world):
                    if current.get(pid):
                        runs.setdefault(pid, []).append(current[pid])
                        run_phase.setdefault(pid, []).append(
                            dict(cur_phase.get(pid, {}))
                        )
                        if current[pid] > 250 and pid in ring:
                            long_runs.append({
                                "pid": pid,
                                "frames": current[pid],
                                "ring": list(ring[pid]),
                                "phases": dict(cur_phase.get(pid, {})),
                            })
                        current[pid] = 0
                        cur_phase[pid] = {}
                    continue
                _cur_live.add(pid)
                current[pid] = current.get(pid, 0) + 1
                _ph = cur_phase.setdefault(pid, {})
                _ph[engine_phase] = _ph.get(engine_phase, 0) + 1
                _ring = ring.get(pid)
                if _ring is None:
                    _ring = _deque(maxlen=12)
                    ring[pid] = _ring
                _ring.append((
                    int(world.frame_count),
                    engine_phase,
                    round(float(getattr(d, "target_x", 0.0) or 0.0)
                          - float(getattr(d, "x", 0.0) or 0.0), 2),
                    round(float(getattr(d, "x", 0.0) or 0.0), 2),
                ))
                # GROUND-TRUTH CLOSURE, CONSECUTIVE FRAMES ONLY: a body that
                # left and re-entered the obligation leaves a position gap
                # spanning the frames it was NOT obligated, which would
                # inflate |x - prev_x| / dt far past the physical ceiling.
                # The effective closure is measured only on frames where the
                # body was obligated on the previous frame too.
                _x_now = float(getattr(d, "x", 0.0) or 0.0)
                if pid in prev_live:
                    _x_prev = prev_x.get(pid, _x_now)
                    eff_speed = abs(_x_now - _x_prev) / DT
                else:
                    eff_speed = None
                prev_x[pid] = _x_now
                _tally_frame(tally, world, ope, d, engine_phase, eff_speed)
                deficit = float(getattr(d, "target_x", 0.0) or 0.0) - \
                    float(getattr(d, "x", 0.0) or 0.0)
                spd = (float(getattr(d, "vx", 0.0) or 0.0) ** 2 +
                       float(getattr(d, "vy", 0.0) or 0.0) ** 2) ** 0.5
                if (len(trace) < trace_limit
                        and deficit > _STALL_DEFICIT_M
                        and spd < _STALL_SPEED_MPS):
                    trace.append(_trace_sample(world, d, engine_phase, pid))
            prev_live = _cur_live

        for pid, frames in current.items():
            if frames:
                runs.setdefault(pid, []).append(frames)
                run_phase.setdefault(pid, []).append(
                    dict(cur_phase.get(pid, {}))
                )
    tally["runs"] = runs
    tally["run_phase"] = run_phase
    tally["long_runs"] = long_runs
    tally["trace"] = trace
    return tally


def _pct(n, total):
    return (100.0 * n / total) if total else 0.0


def _dominant(t, sf):
    """Name the dominant collapse cause across the stall frames."""
    if not sf:
        return "none"
    cands = {
        "move_speed<=0 (command zeroed)": t["stall_move_speed_zero"],
        "frozen": t["stall_frozen"],
        "freeze_timer>0": t["stall_freeze_timer"],
        "PTB engine hard-stop (vx==vy==0)": t["stall_ptb_engine_stop"],
        "at target": t["stall_at_target"],
        "no seed": t["stall_no_seed"],
        "mmdl_moved": t["stall_mmdl_moved"],
    }
    best = max(cands.items(), key=lambda kv: kv[1])
    return "%s (%d/%d stall frames)" % (best[0], best[1], sf)


def main():
    _apply_levers()
    print("=" * 100)
    print("RETREAT LOCOMOTION COLLAPSE PROBE")
    print("=" * 100)
    print("  frames/rollout : %d   rollouts : %d   dt : %.6f"
          % (FRAMES, ROLLOUTS, DT))
    print("  stall signature: deficit > %.2f m AND speed < %.3f m/s"
          % (_STALL_DEFICIT_M, _STALL_SPEED_MPS))
    print()

    total = _blank_tally()
    all_runs = {}
    all_run_phase = {}
    all_long_runs = []
    trace = []
    for i in range(1, ROLLOUTS + 1):
        r = run_rollout(i)
        for k, v in r.items():
            if k in ("deficits", "runs", "trace", "sources", "phases",
                     "soft_by_phase", "eff_speed_by_phase",
                     "commanded_by_phase", "run_phase", "long_runs"):
                continue
            total[k] = total.get(k, 0) + v
        total["deficits"].extend(r["deficits"])
        for k, v in r["soft_by_phase"].items():
            total["soft_by_phase"][k] = total["soft_by_phase"].get(k, 0) + v
        for k, v in r["eff_speed_by_phase"].items():
            total["eff_speed_by_phase"].setdefault(k, []).extend(v)
        for k, v in r["commanded_by_phase"].items():
            total["commanded_by_phase"].setdefault(k, []).extend(v)
        for k, v in r["sources"].items():
            total["sources"][k] = total["sources"].get(k, 0) + v
        for k, v in r["phases"].items():
            total["phases"][k] = total["phases"].get(k, 0) + v
        for pid, runs in r["runs"].items():
            all_runs.setdefault(pid, []).extend(runs)
        for pid, comps in r["run_phase"].items():
            all_run_phase.setdefault(pid, []).extend(comps)
        all_long_runs.extend(r["long_runs"])
        if len(trace) < 14:
            trace.extend(r["trace"][:14 - len(trace)])
        print("  rollout %d: %6d obligated body-frames  %6d stall frames"
              % (i, r["body_frames"], r["stall_frames"]))

    bf = total["body_frames"]
    sf = total["stall_frames"]

    print()
    print("-" * 100)
    print("1. IS THE STALL REAL?  (obligated body holds a target it never reaches)")
    print("-" * 100)
    print("  obligated body-frames           : %d" % bf)
    print("  STALL frames (deficit>0.2,v<.01) : %d  (%.1f%%)"
          % (sf, _pct(sf, bf)))
    if total["deficits"]:
        print("  stall deficit (m)               : n=%d min=%.2f median=%.2f "
              "mean=%.2f max=%.2f"
              % (len(total["deficits"]), min(total["deficits"]),
                 statistics.median(total["deficits"]),
                 statistics.mean(total["deficits"]),
                 max(total["deficits"])))
    print("  verdict: %s"
          % ("STALL CONFIRMED" if sf else "no stall observed"))

    print()
    print("-" * 100)
    print("2. CAUSE TALLY over ALL obligated body-frames")
    print("-" * 100)
    for label, key in (
        ("move_speed <= 0 (hard-return gate)", "move_speed_zero"),
        ("frozen", "frozen"),
        ("freeze_timer > 0", "freeze_timer"),
        ("_mmdl_moved (integrator skipped)", "mmdl_moved"),
        ("at target (|tx-x| <= 0.05)", "at_target"),
        ("no seed (role/seeded/rdtx)", "no_seed"),
    ):
        print("  %-36s %6d  (%.1f%%)"
              % (label, total[key], _pct(total[key], bf)))

    print()
    print("-" * 100)
    print("3. CAUSE TALLY over STALL frames only  <-- THE DISCRIMINATOR")
    print("-" * 100)
    if sf:
        for label, key in (
            ("move_speed <= 0", "stall_move_speed_zero"),
            ("frozen", "stall_frozen"),
            ("freeze_timer > 0", "stall_freeze_timer"),
            ("_mmdl_moved", "stall_mmdl_moved"),
            ("at target", "stall_at_target"),
            ("no seed", "stall_no_seed"),
            ("in PTB_PREP/EXEC/PTB", "stall_ptb_phase"),
            ("PTB phase AND vx==vy==0 exactly", "stall_ptb_engine_stop"),
            ("target_source == 'seed_hold'", "stall_seed_hold_source"),
            ("target_source startswith 'retreat'", "stall_retreat_source"),
        ):
            print("  %-36s %6d  (%.1f%% of stalls)"
                  % (label, total[key], _pct(total[key], sf)))
    else:
        print("  (no stall frames)")

    print()
    print("-" * 100)
    print("4. target_source + engine phase DISTRIBUTION (obligated body-frames)")
    print("-" * 100)
    for src, n in sorted(total["sources"].items(),
                         key=lambda kv: kv[1], reverse=True)[:10]:
        print("  src   %-32s %6d  (%.1f%%)" % (src or "<none>", n, _pct(n, bf)))
    for ph, n in sorted(total["phases"].items(),
                        key=lambda kv: kv[1], reverse=True)[:12]:
        print("  phase %-32s %6d  (%.1f%%)" % (ph or "<none>", n, _pct(n, bf)))

    print()
    print("-" * 100)
    print("5. FIRST STALL FRAMES (state at END of frame)")
    print("-" * 100)
    if trace:
        print("  frame phase            id      x  anchor  target  deficit"
              "     vx     vy  mv_spd state      src")
        for s in trace:
            print("  %5d %-16s %4s %6.2f %6.2f %7.2f %7.2f %6.2f %6.2f %6.2f "
                  "%-10s %s"
                  % (s["frame"], s["engine_phase"], s["pid"], s["x"],
                     s["anchor"], s["target_x"], s["deficit"], s["vx"],
                     s["vy"], s["move_speed"], s["state"], s["src"]))
            print("        frozen=%s ftimer=%.2f mmdl_moved=%s depth_role=%s "
                  "seeded=%s rdtx=%s"
                  % (s["frozen"], s["ftimer"], s["mmdl_moved"],
                     s["depth_role"], s["seeded"], s["rdtx"]))
    else:
        print("  (no stall frame captured)")

    print()
    print("-" * 100)
    print("5b. EFFECTIVE CLOSURE BY PHASE (ground truth |x - prev_x| / dt)")
    print("-" * 100)
    print("  soft = deficit > 0.2 m AND closure < 50% of the commanded speed")
    print("  %-20s %7s %10s %10s %10s %9s"
          % ("phase", "frames", "eff mean", "cmd mean", "eff/cmd", "soft%"))
    for ph in sorted(total["eff_speed_by_phase"],
                     key=lambda p: -len(total["eff_speed_by_phase"][p])):
        eff = total["eff_speed_by_phase"][ph]
        cmd = total["commanded_by_phase"].get(ph, [])
        if not eff:
            continue
        e_mean = statistics.mean(eff)
        c_mean = statistics.mean(cmd) if cmd else 0.0
        ratio = (e_mean / c_mean) if c_mean > 0 else 0.0
        soft = total["soft_by_phase"].get(ph, 0)
        print("  %-20s %7d %10.3f %10.3f %10.3f %8.1f%%"
              % (ph, len(eff), e_mean, c_mean, ratio, _pct(soft, len(eff))))

    flat = [f for runs in all_runs.values() for f in runs]
    print()
    print("-" * 100)
    print("6. OBLIGATION RUN LENGTHS (discharge check)")
    print("-" * 100)
    if flat:
        secs = [f * DT for f in flat]
        over = [s for s in secs if s > 5.0]
        print("  runs=%d  distinct bodies=%d" % (len(secs), len(all_runs)))
        print("  run length (s): min=%.3f median=%.3f mean=%.3f max=%.3f"
              % (min(secs), statistics.median(secs), statistics.mean(secs),
                 max(secs)))
        print("  runs > 5.0 s : %d%s"
              % (len(over), (" (worst %.2f s)" % max(over)) if over else ""))
        print("  verdict: %s"
              % ("DISCHARGES" if not over else "PERMANENT-STALL RISK"))
        # ── LONGEST RUNS: WHICH PHASES DO THEY SPAN? ──
        # A run that spans a TES chamber is a legitimate hold (completion is
        # forbidden while `world.tes_active`); a run spent in a retreat era
        # is a genuine collapse.  This separates the two.
        _flat_runs = []
        for _pid, _comps in all_run_phase.items():
            _lens = all_runs.get(_pid, [])
            for _i, _c in enumerate(_comps):
                _f = _lens[_i] if _i < len(_lens) else 0
                _flat_runs.append((_f, _pid, _c))
        _flat_runs.sort(key=lambda t: t[0], reverse=True)
        print("  longest runs (frames -> s) and the phases they span:")
        for _f, _pid, _c in _flat_runs[:4]:
            _tes = sum(_n for _p, _n in _c.items()
                       if _p in ("TACKLE_INIT", "TACKLE_CONTACT",
                                 "TACKLE_DRIVE", "TACKLE_DOMINANCE"))
            _ret = _f - _tes
            print("    id=%-5s %5d (%5.2f s)  TES-frames=%d  retreat-frames=%d"
                  % (_pid, _f, _f * DT, _tes, _ret))
            print("      %s" % ", ".join(
                "%s=%d" % (p, n)
                for p, n in sorted(_c.items(), key=lambda kv: -kv[1])
            ))
        # ── LONG-RUN DEFICIT TRAJECTORIES ──
        # Does the deficit CLOSE (the anchor is re-armed by the next tackle)
        # or sit still (stuck short of the line)?  The last twelve samples of
        # each long run answer it directly.
        print()
        print("  long-run deficit trajectories (last 12 obligated frames):")
        if not all_long_runs:
            print("    (no run longer than 250 frames)")
        for _lr in sorted(all_long_runs, key=lambda d: -d["frames"])[:3]:
            print("    id=%s frames=%d (%.2f s)"
                  % (_lr["pid"], _lr["frames"], _lr["frames"] * DT))
            for _fr, _ph, _def, _x in _lr["ring"]:
                print("      f=%-5d %-18s x=%7.2f  deficit(tx-x)=%7.2f"
                      % (_fr, _ph, _x, _def))
    else:
        print("  no obligation runs observed")

    print()
    print("=" * 100)
    print("RESULT: stall_frames=%d  dominant_cause=%s" % (sf, _dominant(total, sf)))
    print("=" * 100)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        pygame.quit()
