"""probe_tes_lane_true.py — does TES DRIVE enforce a LANE-TRUE retreat?

Boots the engine headless (kickoff -> catch -> OPEN_PLAY -> tackle -> TES
drive) exactly like ``probe_tes_line_width.py`` and measures, on a FIXED
COHORT of the line bodies that carry a TES structure hold, whether the
lane-true contract holds:

  1. THE DRIVE-ENTRY TABLE — per cohort body on the FIRST drive frame:
     depth_role, x, y, the locked lane (``_tes_lane_y``), the effective
     hold Y (``_tes_hold_y``), target_x/target_y, the lateral error
     ``|y - lane|``, the depth error ``|x - hold_x|``, movement_state.
     This frame decides whether "in-lane defenders hold perfectly".

  2. PER-CHAMBER DRIFT — a CHAMBER is one continuous stretch with an
     unchanged lane seed.  When the authority re-seeds (a new tackle
     chamber, or the chamber-gap watchdog firing after a RELEASE / PTB
     stretch) EVERY lane changes at once, so each chamber is measured
     against ITS OWN lanes.  Reported per chamber: the seed frame, the
     held span at seed and its min/max, and the worst per-body
     ``|y - lane|`` (with the frame's role / held / movement state).

  3. HELD / FROZEN COUNTS across the whole drive.

Exit 0 = LANE-TRUE: every cohort body's seed lane equals its own
         drive-entry Y, every body stays within DRIFT_TOLERANCE_M of its
         own lane inside every chamber, and no chamber's held span grows
         by more than SPAN_GROWTH_TOLERANCE_M (the line does not fan).
Exit 1 = the contract is violated (numbers printed for attribution).
Exit 2 = no drive frame observed.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

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
from game.defence.defence_lvl_1 import tes_drive_structure as tds

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
DRIFT_TOLERANCE_M = 1.5
SPAN_GROWTH_TOLERANCE_M = 1.5
SEED_TOLERANCE_M = 0.05
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_tes_lane_true_engine.log"


def _role_name(player):
    role = getattr(player, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _mstate(player):
    ms = getattr(player, "movement_state", None)
    return getattr(ms, "name", None) or (str(ms) if ms is not None else None)


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
    return world, ball, open_play, kickoff_engine, collision_system, field


def line_bodies(world):
    """The defensive LINE bodies under test (no tacklers / markers / FB)."""
    bc = world.ball_carrier
    out = []
    for d in world.defending_team:
        if _role_name(d) not in LINE_ROLES:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if d is bc:
            continue
        out.append(d)
    return out


def held_bodies(world):
    return [d for d in line_bodies(world)
            if bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False))]


def snapshot_body(d):
    lane = getattr(d, tds.LANE_Y_ATTR, None)
    hold_y = getattr(d, tds.HOLD_Y_ATTR, None)
    hold_x = getattr(d, tds.HOLD_X_ATTR, None)
    lat_err = None
    if lane is not None:
        lat_err = abs(float(d.y) - float(lane))
    dep_err = None
    if hold_x is not None:
        dep_err = abs(float(d.x) - float(hold_x))
    return {
        "pid": getattr(d, "player_id", "?"),
        "def_role": _role_name(d),
        "depth_role": getattr(d, "depth_role", None),
        "x": float(getattr(d, "x", 0.0)),
        "y": float(getattr(d, "y", 0.0)),
        "lane": lane,
        "hold_y": hold_y,
        "hold_x": hold_x,
        "target_x": getattr(d, "target_x", None),
        "target_y": getattr(d, "target_y", None),
        "src": getattr(d, "target_source", None),
        "lat_err": lat_err,
        "dep_err": dep_err,
        "hold": bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)),
        "mstate": _mstate(d),
    }
def _cohort_span(world, pids):
    """Span (max y - min y) across the tracked cohort bodies this frame."""
    ys = []
    for d in world.defending_team:
        if getattr(d, "player_id", None) in pids:
            ys.append(float(getattr(d, "y", 0.0)))
    if len(ys) < 2:
        return None
    return max(ys) - min(ys)


def _body_by_pid(world, pid):
    for d in world.defending_team:
        if getattr(d, "player_id", None) == pid:
            return d
    return None


def _open_chamber(world, track, frame, span_now):
    """Start a fresh chamber: every body's lane becomes the live seed."""
    for pid, t in track.items():
        d = _body_by_pid(world, pid)
        if d is None:
            t["lane"] = None
            continue
        lane = getattr(d, tds.LANE_Y_ATTR, None)
        t["lane"] = lane
        t["worst_dev"] = (abs(float(d.y) - float(lane))
                          if lane is not None else 0.0)
        t["worst"] = snapshot_body(d)
    return {
        "seed_frame": frame,
        "frames": 0,
        "span_seed": span_now,
        "span_min": span_now,
        "span_max": span_now,
        "worst_dev": max((t["worst_dev"] for t in track.values()),
                         default=0.0),
        "worst_pid": None,
        "frozen_frames": 0,
    }


def main():
    print("=" * 78)
    print("TES LANE-TRUE RETREAT PROBE — do in-lane defenders hold, and does")
    print("the held line leave its own lane / fan across the chamber?")
    print("=" * 78)

    buf = io.StringIO()
    result = {}
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system, field = \
            boot_world()
        dt = 1.0 / 60.0

        entry = None        # (frame, phase, ruck_y, ruck_x, [snapshots])
        cohort = {}         # {pid: first-chamber snapshot}
        track = {}          # {pid: {"lane","worst_dev","worst"}}
        chambers = []
        cur = None
        held_frames = 0
        frozen_frames = 0
        drive_frames = 0
        reseeds = 0
        trace = []

        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = open_play._phase
            if phase not in DRIVE_PHASES:
                continue

            drive_frames += 1
            held = held_bodies(world)
            held_frames += len(held)
            frozen_frames += sum(1 for d in held if _mstate(d) == "HOLD")
            if len(trace) < 60:
                _p207 = _body_by_pid(world, 207)
                trace.append(
                    f"f={world.frame_count:<5} ph={str(phase):<17}"
                    f"sub={str(getattr(world, 'sub_state', None)):<24}"
                    f"chamber={str(getattr(world, tds.CHAMBER_ID_ATTR, None)):<5}"
                    f"held={len(held):<3}"
                    f"lane207={str(getattr(_p207, tds.LANE_Y_ATTR, None) if _p207 else None):<21}"
                    f"y207={float(getattr(_p207, 'y', 0.0)) if _p207 else 0.0:.2f}"
                )

            if entry is None:
                entry = (
                    world.frame_count, phase,
                    float(getattr(world, "ruck_y_m", 35.0) or 35.0),
                    float(getattr(world, "ruck_x_m", 60.0) or 60.0),
                    [snapshot_body(d) for d in line_bodies(world)],
                )
                cohort = {s["pid"]: s for s in entry[4]}
                track = {pid: {"lane": None, "worst_dev": 0.0, "worst": dict(s)}
                         for pid, s in cohort.items()}
                span_now = _cohort_span(world, set(track))
                cur = _open_chamber(world, track, world.frame_count, span_now)
                reseeds += 1
                continue

            span_now = _cohort_span(world, set(track))

            # ── chamber detection: any lane re-seed starts a NEW chamber ──
            fresh = False
            for pid, t in track.items():
                d = _body_by_pid(world, pid)
                if d is None:
                    continue
                lane = getattr(d, tds.LANE_Y_ATTR, None)
                if lane is None:
                    continue
                if t["lane"] is None or abs(float(lane) - float(t["lane"])) > 1e-9:
                    fresh = True
                    break
            if fresh:
                chambers.append(cur)
                cur = _open_chamber(world, track, world.frame_count, span_now)
                reseeds += 1
                continue

            cur["frames"] += 1
            if span_now is not None:
                if span_now < cur["span_min"]:
                    cur["span_min"] = span_now
                if span_now > cur["span_max"]:
                    cur["span_max"] = span_now
            for pid, t in track.items():
                d = _body_by_pid(world, pid)
                if d is None or t["lane"] is None:
                    continue
                if bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)):
                    cur["frozen_frames"] += (
                        1 if _mstate(d) == "HOLD" else 0
                    )
                dev = abs(float(d.y) - float(t["lane"]))
                if dev > t["worst_dev"]:
                    t["worst_dev"] = dev
                    t["worst"] = snapshot_body(d)
                if dev > cur["worst_dev"]:
                    cur["worst_dev"] = dev
                    cur["worst_pid"] = pid

        if cur is not None:
            chambers.append(cur)

        result = {
            "entry": entry, "cohort": cohort, "track": track,
            "chambers": chambers, "held_frames": held_frames,
            "frozen_frames": frozen_frames, "drive_frames": drive_frames,
            "reseeds": reseeds, "trace": trace,
        }

    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    if result["drive_frames"] == 0 or result["entry"] is None:
        print("\nRESULT: FAILED — no TES drive frame observed.")
        return 2

    frame, phase, ruck_y, ruck_x, snaps = result["entry"]
    entry_cohort = result["cohort"]
    chambers = result["chambers"]
    track = result["track"]

    print(f"\ndrive frames observed : {result['drive_frames']}")
    print(f"cohort size           : {len(entry_cohort)}")
    print(f"chambers (lane seeds) : {result['reseeds']}")
    print(f"held-body-frames      : {result['held_frames']}")
    print(f"frozen-body-frames    : {result['frozen_frames']}")

    print(f"\n-- COHORT ON THE FIRST DRIVE FRAME (#{frame} {phase}) --")
    print(f"  {'pid':<5}{'depth':<7}{'x':>9}{'y':>9}{'lane':>9}"
          f"{'hold_y':>9}{'tgt_x':>9}{'tgt_y':>9}{'lat_err':>9}"
          f"{'dep_err':>9}{'src':<16}{'mstate':<8}")
    seed_true = True
    n_hold = 0
    n_drift = 0
    for s in sorted(entry_cohort.values(), key=lambda r: -r["y"]):
        fmt = lambda v: "-" if v is None else f"{float(v):.2f}"
        le = "-" if s["lat_err"] is None else f"{s['lat_err']:.3f}"
        de = "-" if s["dep_err"] is None else f"{s['dep_err']:.2f}"
        print(f"  {str(s['pid']):<5}{str(s['depth_role']):<7}"
              f"{s['x']:>9.2f}{s['y']:>9.2f}{fmt(s['lane']):>9}"
              f"{fmt(s['hold_y']):>9}{fmt(s['target_x']):>9}"
              f"{fmt(s['target_y']):>9}{le:>9}{de:>9}"
              f"{str(s['src']):<16}{str(s['mstate']):<8}")
        if s["lane"] is None or abs(float(s["lane"]) - s["y"]) > SEED_TOLERANCE_M:
            seed_true = False
        if s["mstate"] == "HOLD":
            n_hold += 1
        elif s["mstate"] == "DRIFT":
            n_drift += 1

    print(f"\nentry-frame states: HOLD={n_hold} DRIFT={n_drift} "
          f"other={len(entry_cohort) - n_hold - n_drift}")

    if result.get("trace"):
        print("\n-- DISPATCH TRACE (first drive frames) --")
        for _ln in result["trace"]:
            print("  " + _ln)
    _n_rows = len(chambers)
    if _n_rows > 25:
        print(f"\n-- PER-CHAMBER DRIFT: {_n_rows} chambers; showing the first "
              f"12 and the last 5 --")
    else:
        print("\n-- PER-CHAMBER DRIFT (each chamber measured against its OWN "
              "lanes) --")
    print(f"  {'#':<4}{'seed_frm':>9}{'frames':>8}{'span_seed':>11}"
          f"{'span_min':>10}{'span_max':>10}{'growth':>9}{'worst_dev':>11}"
          f"  worst pid (role/held/state)")
    worst_overall = 0.0
    worst_growth = 0.0
    worst_chamber_pid = None
    for i, c in enumerate(chambers):
        span_seed = c["span_seed"] or 0.0
        growth = max(0.0, (c["span_max"] or 0.0) - span_seed)
        worst_growth = max(worst_growth, growth)
        pid = c["worst_pid"]
        w = track[pid]["worst"] if pid in track else None
        tag = "-"
        if w is not None:
            tag = f"{str(pid)} ({w['def_role']}/{w['hold']}/{w['mstate']})"
        if i < 12 or i >= _n_rows - 5:
            print(f"  {i + 1:<4}{c['seed_frame']:>9}{c['frames']:>8}"
                  f"{span_seed:>11.2f}{c['span_min']:>10.2f}"
                  f"{c['span_max']:>10.2f}{growth:>9.2f}"
                  f"{c['worst_dev']:>11.2f}  {tag}")
        if c["worst_dev"] > worst_overall:
            worst_overall = c["worst_dev"]
            worst_chamber_pid = pid

    print(f"\nmax per-body deviation from its own chamber lane : "
          f"{worst_overall:.2f} m (tolerance {DRIFT_TOLERANCE_M} m)")
    print(f"max chamber held-span growth (fan)              : "
          f"{worst_growth:.2f} m (tolerance {SPAN_GROWTH_TOLERANCE_M} m)")
    print(f"seed lane == own drive-entry Y (<= {SEED_TOLERANCE_M} m)   : "
          f"{seed_true}")

    print("\n" + "=" * 78)
    ok = (seed_true
          and worst_overall <= DRIFT_TOLERANCE_M
          and worst_growth <= SPAN_GROWTH_TOLERANCE_M)
    if ok:
        print("RESULT: LANE-TRUE — every body holds its own lane in every "
              "chamber; the line neither drifts nor fans.")
        return 0
    print("RESULT: LANE-TRUE CONTRACT VIOLATED — see the tables above.")
    return 1


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
