"""tmp/probe_stage4b_exit_ownership.py — Stage 4B EXIT-BOUNDARY AUDIT PROBE.

WHAT IT MEASURES
----------------
Four independent questions about the TES -> line exit boundary, in ONE run:

  Q1 EXIT TIMING      How long does each exit sub-phase actually take, and
                      does it exit on its GATE or on its WATCHDOG?  Measured
                      against world/rules/exit_timing_budget.py.

  Q2 PUBLISH COUNT    How many times is ONE frame's spacing/width geometry
                      solved?  Read from the Stage-4B provenance counter
                      (defensive_pipeline.stamp_frame_geometry_publish).

  Q3 MULTI-WRITER     Which defenders have MORE THAN ONE distinct writer
                      touch their target in a single exit frame?  Read from
                      the engine's OWN write log (world._target_write_log,
                      installed here) so the count is real per-write data,
                      not a from-outside guess.

  Q4 RELEASE -> LINE  For every body the tackle-state gate RELEASED, when did
                      it leave TES ownership, when did it rejoin the line
                      population, and did the frame's LAST writer (the
                      ruck-adjacent pin) touch it on the release frame?

METHOD
------
Read-only.  Boots the real engine headless (identical bootstrap to
probe_phase_timer_budget.py / probe_stage2_depth_delivery.py), steps the
match, and records one observation per frame.  It installs
``world._target_write_log`` so ``stamp_target_write`` records every write;
the list is CLEARED at the start of each frame so memory is bounded to one
frame of records.

NOTHING in production is modified by this probe.

Run:  python tmp/probe_stage4b_exit_ownership.py
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

from game.defence.defence_lvl_1 import exit_transition_authority as eta
from game.defence.defence_lvl_1.defensive_pipeline import (
    frame_geometry_publish_sources,
)
from world.rules import exit_timing_budget as etb

DT = 1.0 / 60.0
MAX_FRAMES = 3600
ROLLOUTS = 3

TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")
EXIT_PHASES = ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
               "POST_RUCK_OPEN_PLAY")


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
    # Install the engine's own write log so stamp_target_write records every
    # write.  audit_on() is True whenever this attribute exists.
    world._target_write_log = []
    return world, ball, open_play, kickoff_engine, collision_system


# ─────────────────────────────────────────────────────────────────────
# SNAPSHOT HELPERS
# ─────────────────────────────────────────────────────────────────────

def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def defender_rows(world):
    """Per-defender observation for the current frame-end state."""
    rows = {}
    for d in (getattr(world, "defending_team", None) or []):
        if d is None:
            continue
        cls = eta.body_class(d, world)
        chans = eta.ownership_channels(world, d)
        rows[id(d)] = {
            "pid": getattr(d, "player_id", None),
            "class": cls,
            "role": getattr(getattr(d, "defender_role", None), "name", None),
            "tackle_state": bool(getattr(d, "tackle_state_active", False)),
            "depth_role": getattr(d, "depth_role", None),
            "x": _f(getattr(d, "x", 0.0)),
            "y": _f(getattr(d, "y", 0.0)),
            "target_x": _f(getattr(d, "target_x", 0.0)),
            "target_y": _f(getattr(d, "target_y", 0.0)),
            "frame_spacing_y": getattr(d, "frame_spacing_y", None),
            "target_writer": chans["target_writer"],
            "target_source": chans["target_source"],
            "channels_disagree": chans["channels_disagree"],
        }
    return rows


def pin_slot_ids(world):
    """Player ids the ruck-adjacent pin owns this frame (L/R/L2/R2)."""
    state = getattr(world, "_ruck_pin_state", None)
    if not isinstance(state, dict):
        return set()
    return {v for v in state.values() if v is not None}


def writes_this_frame(world):
    """{(pid): [writer, ...]} from the engine's own write log, this frame."""
    store = getattr(world, "_target_write_log", None)
    if not store:
        return {}
    out = defaultdict(list)
    for rec in store:
        pid = rec.get("player_id")
        if pid is None:
            continue
        out[pid].append(rec.get("writer"))
    return out


def in_line_population(world):
    """The set of Player ids the single population authority counts."""
    try:
        from game.defence.defence_lvl_1.population_authority import (
            line_population,
        )
        team = getattr(world, "defending_team", None) or []
        return {id(d) for d in line_population(team)}
    except Exception:
        return set()
# ─────────────────────────────────────────────────────────────────────
# CYCLE BOOKKEEPING
# ─────────────────────────────────────────────────────────────────────

CHAIN = TES_PHASES + EXIT_PHASES


def new_cycle(frame):
    return {
        "start": frame,
        "spans": {},
        "active": None,
        "exit_rows": [],
        "publishes": Counter(),
        "released": [],          # (frame, pid, class)
        "multiwriter": [],       # (frame, phase, pid, writers)
        "pin_on_release": [],    # (frame, pid, dy_before, dy_after)
        "transitions": {},       # pid -> {"left_tes", "joined_line", "class"}
        "ownership_rows": Counter(),
    }


def note_span(cur, phase, frame):
    span = cur["spans"].get(phase)
    if span is None:
        span = {"first": frame, "last": frame, "frames": 0}
        cur["spans"][phase] = span
    span["last"] = frame
    span["frames"] += 1
    return span


# ─────────────────────────────────────────────────────────────────────
# ONE ROLLOUT
# ─────────────────────────────────────────────────────────────────────

def run_rollout(out):
    """Step one match; aggregate into *out* (a mutable stats dict)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()
        prev = None            # previous frame-end state, by id(d)
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

            world._target_write_log.clear()
            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = str(ope._phase)
            frame = int(world.frame_count)
            post = defender_rows(world)
            writes = writes_this_frame(world)
            pins = pin_slot_ids(world)
            members = in_line_population(world)
            pub_count, pub_sources = frame_geometry_publish_sources(world)

            if cur is None and phase in TES_PHASES:
                cur = new_cycle(frame)
            if cur is not None:
                _observe(cur, out, world, prev, post, writes, pins, members,
                         pub_count, pub_sources, phase, frame)
                if phase not in CHAIN:
                    _close(cur, out, frame)
                    cur = None
                    out["cycles"] += 1
            prev = post
        if cur is not None:
            _close(cur, out, int(world.frame_count))


def _observe(cur, out, world, prev, post, writes, pins, members,
             pub_count, pub_sources, phase, frame):
    """Record one exit-era frame; update the per-body transition table."""
    in_exit = phase in EXIT_PHASES or (
        phase == "OPEN_PLAY" and cur["spans"].get("POST_RUCK_OPEN_PLAY")
    )
    if in_exit:
        span = note_span(cur, phase if phase != "OPEN_PLAY"
                         else "OPEN_PLAY_ENTRY", frame)
        cur["publishes"][pub_count] += 1
        out["publishes_by_phase"][phase][pub_count] += 1
        if pub_count > 1:
            out["multi_publish"].append(
                (phase, frame, tuple(pub_sources))
            )
        sub = phase if phase in eta.EXIT_SUBPHASES else "OPEN_PLAY_ENTRY"
        for row in eta.audit_exit_frame(world, sub):
            kind = row.split(" ", 1)[0]
            cur["ownership_rows"][kind] += 1
            out["ownership_rows"][kind] += 1
            # Normalized key: drop the id= token so rows aggregate.
            out["audit_row_keys"][
                " ".join(tok for tok in row.split()
                         if not tok.startswith("id="))
            ] += 1

    # ── multi-writer detection (real per-write data from the engine log) ──
    if in_exit:
        for pid, writer_seq in writes.items():
            distinct = {w for w in writer_seq if w}
            if len(distinct) > 1:
                body = next((r for r in post.values()
                             if r["pid"] == pid), None)
                entry = (frame, phase, pid, tuple(sorted(distinct)))
                cur["multiwriter"].append(entry)
                out["multiwriter"].append(entry + (body["class"]
                                                   if body else "?",))
                out["writer_pairs"][tuple(sorted(distinct))] += 1

    # ── per-body transition table + channel disagreement + release ──
    for key, row in post.items():
        old = (prev or {}).get(key)
        rec = cur["transitions"].setdefault(
            row["pid"], {"left_tes": None, "joined_line": None,
                         "class": row["class"]}
        )
        if old is not None and old["tackle_state"] and not row["tackle_state"]:
            rec["left_tes"] = frame
            rec["class"] = row["class"]
            cur["released"].append((frame, row["pid"], row["class"]))
            out["released_total"] += 1
            out["released_by_class"][row["class"]] += 1
            # Did the pin own this body on its release frame?
            if row["pid"] in pins:
                out["pin_on_release"] += 1
                out["pin_release_y_delta"].append(
                    abs(row["y"] - (old["y"] if old else row["y"]))
                )
        joined = key in members
        if joined and rec["joined_line"] is None:
            rec["joined_line"] = frame
        row["in_line"] = joined
        if row["channels_disagree"]:
            out["channel_disagree"] += 1

    # ── pin impact on every body during the exit era ──
    if in_exit:
        for key, row in post.items():
            old = (prev or {}).get(key)
            if old is None:
                continue
            if row["pid"] in pins:
                out["pin_frames"] += 1
                out["pin_abs_dy"].append(abs(row["y"] - old["y"]))


def _close(cur, out, frame):
    """Flush one cycle's measured durations and handover delays into *out*."""
    cur["end"] = frame
    for phase, span in cur["spans"].items():
        if phase == "OPEN_PLAY_ENTRY":
            continue
        out["phase_durations"][phase].append(span["frames"] * DT)
    total = 0.0
    seen = 0
    for phase in etb.EXIT_BUDGET_ORDER:
        span = cur["spans"].get(phase)
        if span is None:
            continue
        total += span["frames"] * DT
        seen += 1
    if seen >= 3:
        out["cycle_exit_total_s"].append(total)
    for _pid, rec in cur["transitions"].items():
        left = rec.get("left_tes")
        joined = rec.get("joined_line")
        if left is not None and joined is not None:
            out["join_delay_frames"].append(max(0, joined - left))


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


def report_timing(out):
    print("=" * 100)
    print("Q1  EXIT TIMING vs THE DECLARED BUDGET")
    print("=" * 100)
    print("  declared envelope: %s" % etb.format_budget())
    print()
    print("  %-20s %4s %7s %7s %7s %7s %7s %6s" % (
        "sub-phase", "n", "min", "median", "mean", "max",
        "declared", "over"))
    for ph in etb.EXIT_BUDGET_ORDER:
        durs = out["phase_durations"][ph]
        st = stats(durs)
        declared = etb.declared_budget_s(ph)
        over = sum(1 for d in durs if declared and d > declared)
        if st is None:
            print("  %-20s %4d %7s %7s %7s %7s %7s %6d" % (
                ph, 0, "-", "-", "-", "-", fmt(declared), 0))
            continue
        print("  %-20s %4d %7s %7s %7s %7s %7s %6d" % (
            ph, st["n"], fmt(st["min"]), fmt(st["median"]), fmt(st["mean"]),
            fmt(st["max"]), fmt(declared), over))
    print()
    total = stats(out["cycle_exit_total_s"])
    if total:
        print("  whole exit era per cycle: n=%d min=%s median=%s mean=%s "
              "max=%s  (declared total %.2fs)"
              % (total["n"], fmt(total["min"]), fmt(total["median"]),
                 fmt(total["mean"]), fmt(total["max"]),
                 etb.total_exit_budget_s()))
    print()


def report_publishes(out):
    print("=" * 100)
    print("Q2  FRAME-GEOMETRY PUBLISH COUNT PER FRAME (same-frame re-solves)")
    print("=" * 100)
    dist = Counter()
    for ph, counts in out["publishes_by_phase"].items():
        for count, n in counts.items():
            dist[count] += n
    print("  publications per exit frame : %s"
          % dict(sorted(dist.items())))
    multi = out["multi_publish"]
    print("  exit frames with >1 publish : %d" % len(multi))
    if multi:
        combos = Counter(src for _ph, _fr, src in multi)
        print("  source combinations         :")
        for src, n in combos.most_common():
            print("      n=%4d  %s" % (n, " + ".join(src)))
    print()


def report_multiwriter(out):
    print("=" * 100)
    print("Q3  MULTI-WRITER EVENTS (one body, >1 distinct writer, one frame)")
    print("=" * 100)
    print("  events total            : %d" % len(out["multiwriter"]))
    print("  distinct writer pairs   : %d" % len(out["writer_pairs"]))
    if out["writer_pairs"]:
        print("  top pairs:")
        for pair, n in out["writer_pairs"].most_common(12):
            print("      n=%5d  %s" % (n, " -> ".join(pair)))
    by_class = Counter(cls for _f, _p, _pid, _w, cls in out["multiwriter"])
    if by_class:
        print("  by body class           : %s" % dict(by_class.most_common()))
    print("  channel disagreements   : %d body-frames where _target_writer "
          "!= target_source" % out["channel_disagree"])
    print()
    if out["ownership_rows"]:
        print("  exit_transition_authority audit rows:")
        for kind, n in out["ownership_rows"].most_common():
            print("      %-26s %6d" % (kind, n))
        print()
    if out["audit_row_keys"]:
        print("  TOP NORMALIZED AUDIT ROWS (what the map does not declare):")
        for key, n in out["audit_row_keys"].most_common(12):
            print("      n=%6d  %s" % (n, key[:160]))
        print()


def report_release(out):
    print("=" * 100)
    print("Q4  TES -> LINE HANDOVER")
    print("=" * 100)
    print("  bodies released from TES ownership : %d" % out["released_total"])
    print("  by class at release                : %s"
          % dict(out["released_by_class"].most_common()))
    delta = stats(out["join_delay_frames"])
    if delta:
        print("  frames between leave-TES and line-membership: n=%d min=%s "
              "median=%s mean=%s max=%s"
              % (delta["n"], fmt(delta["min"], 0), fmt(delta["median"], 0),
                 fmt(delta["mean"]), fmt(delta["max"])))
    print()
    print("  RUCK-PIN ON THE RELEASE FRAME (the pin is the frame's LAST writer)")
    print("    released bodies the pin owned that frame : %d / %d"
          % (out["pin_on_release"], out["released_total"]))
    pin_dy = stats(out["pin_release_y_delta"])
    if pin_dy:
        print("    |dy| on that frame for those bodies      : mean %s  max %s m"
              % (fmt(pin_dy["mean"], 3), fmt(pin_dy["max"], 3)))
    print("    pin-owned body-frames in the exit era    : %d" % out["pin_frames"])
    all_dy = stats(out["pin_abs_dy"])
    if all_dy:
        print("    |dy| per pin-owned exit body-frame       : mean %s  max %s m"
              % (fmt(all_dy["mean"], 3), fmt(all_dy["max"], 3)))
    print()


def main():
    out = {
        "cycles": 0,
        "phase_durations": defaultdict(list),
        "cycle_exit_total_s": [],
        "publishes_by_phase": defaultdict(Counter),
        "multi_publish": [],
        "multiwriter": [],
        "writer_pairs": Counter(),
        "ownership_rows": Counter(),
        "audit_row_keys": Counter(),
        "released_total": 0,
        "released_by_class": Counter(),
        "pin_on_release": 0,
        "pin_release_y_delta": [],
        "pin_frames": 0,
        "pin_abs_dy": [],
        "join_delay_frames": [],
        "channel_disagree": 0,
    }
    for _ in range(ROLLOUTS):
        run_rollout(out)

    print("=" * 100)
    print("STAGE 4B — PTB/TES EXIT-BOUNDARY OWNERSHIP + TIMING AUDIT")
    print("=" * 100)
    print("  rollouts=%d  frames/rollout=%d  tackle cycles=%d  dt=%.6f"
          % (ROLLOUTS, MAX_FRAMES, out["cycles"], DT))
    print("  declared envelope: %s" % etb.format_budget())
    print()
    report_timing(out)
    report_publishes(out)
    report_multiwriter(out)
    report_release(out)
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
