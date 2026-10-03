"""probe_tes_entry_compression.py — is the DEFENSIVE LINE COMPRESSED at TES
chamber entry?

MOTIVATION
----------
``verify_tes_lane_true_retreat.py`` validates that a latched lane does not
change inside a chamber (clause C), that the latch equals the body's OWN
entry Y (clause E), and that gaps between LOCKED lanes do not move (clause D).
Every one of those clauses is measured against the seed it took at entry,
so a line that was ALREADY laterally compressed on the entry frame passes
all of them: clause D compares gaps to the (compressed) seed gaps, and
clause E compares the lane to the body's own (compressed) Y.

Nothing in that verifier asserts that the entry line is CORRECTLY SPACED.
This probe measures the entry geometry against the shipped count-lane
model — ``side_lanes(n, ruck_y, side)`` = ruck_y ± (2.0 + i * defender_gap_m),
uniform 4.0 m gaps — and reports, per drive chamber:

  * the ACTUAL entry Y of every line body and the adjacent gaps between
    them (compression = gap materially below defender_gap_m);
  * the CHAIN TARGET (``d.frame_spacing_y``) at entry and its gaps — the
    geometry the OPEN_PLAY pipeline was DRIVING the bodies toward;
  * the count-lane model lanes for the live per-side count, and the
    per-body error of the actual Y against the nearest model lane.

A body whose actual lane error is large, or a pair whose actual gap is a
fraction of the model gap, is a compressed entry line: the lane-true hold
then LOCKS that compression for the whole chamber.

Exit 0 always (diagnostic).  Numbers are the deliverable.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
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
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_lanes, LEFT, RIGHT,
)

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
MAX_FRAMES = 3000
COMPRESSION_TOL_M = 1.0
ENGINE_LOG = "tmp_tes_entry_compression_engine.log"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _mstate(p):
    ms = getattr(p, "movement_state", None)
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
    return world, ball, open_play, kickoff_engine, collision_system


def line_bodies(world):
    bc = world.ball_carrier
    return [
        d for d in world.defending_team
        if _role_name(d) in LINE_ROLES
        and not getattr(d, "fullback_role_active", False)
        and d is not bc
    ]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _gaps(ys):
    ys = sorted(ys)
    return [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]


def _collect_entry(world, tuners, record):
    """Record the entry geometry of one chamber."""
    bodies = line_bodies(world)
    ruck_y = _f(getattr(world, "ruck_y_m", 35.0))
    ruck_x = _f(getattr(world, "ruck_x_m", 60.0))
    rows = []
    for d in bodies:
        side = getattr(d, "depth_side", None)
        rows.append({
            "pid": getattr(d, "player_id", None),
            "role": _role_name(d),
            "depth_role": getattr(d, "depth_role", None),
            "side": side,
            "x": _f(getattr(d, "x", 0.0)),
            "y": _f(getattr(d, "y", 0.0)),
            "chain_y": getattr(d, "frame_spacing_y", None),
            "tgt_y": getattr(d, "target_y", None),
            "src": str(getattr(d, "target_source", "") or "-"),
            "tsa": bool(getattr(d, "tackle_state_active", False)),
            "mstate": _mstate(d),
            "hold": bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)),
        })
    n_left = sum(1 for r in rows if r["side"] == LEFT)
    n_right = sum(1 for r in rows if r["side"] == RIGHT)
    lanes_left = side_lanes(n_left, ruck_y, LEFT, tuners) if n_left else []
    lanes_right = side_lanes(n_right, ruck_y, RIGHT, tuners) if n_right else []
    model_lanes = list(lanes_left) + list(lanes_right)
    for r in rows:
        cand = lanes_left if r["side"] == LEFT else lanes_right
        if cand:
            r["lane_err"] = min(abs(r["y"] - ly) for ly in cand)
        else:
            r["lane_err"] = None
    actual_ys = [r["y"] for r in rows]
    chain_ys = [_f(r["chain_y"]) for r in rows if r["chain_y"] is not None]
    record.update({
        "ruck_x": ruck_x,
        "ruck_y": ruck_y,
        "rows": rows,
        "n_left": n_left,
        "n_right": n_right,
        "actual_ys": sorted(actual_ys),
        "actual_gaps": _gaps(actual_ys),
        "chain_ys": sorted(chain_ys),
        "chain_gaps": _gaps(chain_ys),
        "model_lanes": sorted(model_lanes),
        "actual_span": (max(actual_ys) - min(actual_ys)) if actual_ys else 0.0,
        "model_span": (max(model_lanes) - min(model_lanes)) if model_lanes else 0.0,
        "chain_span": (max(chain_ys) - min(chain_ys)) if chain_ys else 0.0,
        "max_lane_err": max(
            (r["lane_err"] for r in rows if r["lane_err"] is not None),
            default=0.0,
        ),
    })


def main():
    diag = {"phases": {}, "cids": [], "drive_frames": 0,
            "tes_frames": 0, "last_frame": 0, "kickoff_frames": 0,
            "catch_frames": 0}
    buf = io.StringIO()
    model_gap = 4.0
    records = []
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        tuners = getattr(open_play, "def_tuners", None)
        model_gap = float(getattr(tuners, "defender_gap_m", 4.0))
        dt = 1.0 / 60.0
        prev_cid = None
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                diag["kickoff_frames"] += 1
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                diag["catch_frames"] += 1
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
            diag["last_frame"] = world.frame_count
            diag["phases"][str(phase)] = diag["phases"].get(str(phase), 0) + 1
            if getattr(world, "tes_active", False):
                diag["tes_frames"] += 1
            if phase not in DRIVE_PHASES:
                continue
            diag["drive_frames"] += 1
            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if cid == prev_cid:
                continue
            prev_cid = cid
            if len(diag["cids"]) < 20:
                diag["cids"].append((world.frame_count, cid))
            rec = {"cid": cid, "frame": world.frame_count, "phase": str(phase)}
            _collect_entry(world, tuners, rec)
            records.append(rec)
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    print("=" * 78)
    print("TES CHAMBER-ENTRY LINE-GEOMETRY PROBE (compression audit)")
    print("=" * 78)
    print(f"model gap (defender_gap_m) = {model_gap:.2f} m; "
          f"chambers observed = {len(records)}")
    print(f"DIAG last_frame={diag['last_frame']} "
          f"kickoff_frames={diag['kickoff_frames']} "
          f"catch_frames={diag['catch_frames']} "
          f"drive_frames={diag['drive_frames']} "
          f"tes_frames={diag['tes_frames']}")
    print(f"DIAG phase counts = {diag['phases']}")
    print(f"DIAG first chamber-id transitions = {diag['cids']}")
    if not records:
        print("RESULT: no drive chamber observed.")
        return 0

    worst_by_chamber = []
    for i, rec in enumerate(records):
        print("\n" + "-" * 78)
        print(f"CHAMBER {i + 1}  cid={rec['cid']}  entry frame={rec['frame']} "
              f"phase={rec['phase']}")
        print(f"  ruck=({rec['ruck_x']:.2f},{rec['ruck_y']:.2f})  "
              f"bodies={len(rec['rows'])}  n_left={rec['n_left']} "
              f"n_right={rec['n_right']}")
        print(f"  actual span={rec['actual_span']:.2f}  "
              f"chain span={rec['chain_span']:.2f}  "
              f"model span={rec['model_span']:.2f}  "
              f"max lane err={rec['max_lane_err']:.2f} m")
        print(f"  {'pid':<6}{'role':<15}{'depth':<7}{'side':>5}"
              f"{'x':>8}{'y':>8}{'tgt_y':>8}{'chain_y':>9}{'lane_err':>9}"
              f"{'tsa':>5}{'mstate':<7}{'src':<16}{'hold':>6}")
        for r in sorted(rec["rows"], key=lambda r: -r["y"]):
            side_s = {-1: "L", 1: "R"}.get(r["side"], "-")
            cy = "-" if r["chain_y"] is None else f"{_f(r['chain_y']):.2f}"
            le = "-" if r["lane_err"] is None else f"{r['lane_err']:.2f}"
            ty = "-" if r["tgt_y"] is None else f"{_f(r['tgt_y']):.2f}"
            print(f"  {str(r['pid']):<6}{str(r['role']):<15}"
                  f"{str(r['depth_role']):<7}{side_s:>5}"
                  f"{r['x']:>8.2f}{r['y']:>8.2f}{ty:>8}{cy:>9}{le:>9}"
                  f"{str(r['tsa']):>5}{str(r['mstate']):<7}"
                  f"{str(r['src']):<16}{str(r['hold']):>6}")
        ag = rec["actual_gaps"]
        cg = rec["chain_gaps"]
        print("  actual gaps : " + " ".join(f"{g:.2f}" for g in ag))
        print("  chain  gaps : " + " ".join(f"{g:.2f}" for g in cg))
        min_actual = min(ag) if ag else float("inf")
        min_chain = min(cg) if cg else float("inf")
        print(f"  min actual gap={min_actual if ag else float('nan'):.2f}  "
              f"min chain gap={min_chain if cg else float('nan'):.2f}  "
              f"(model {model_gap:.2f})")
        worst_by_chamber.append(
            (i + 1, min_actual, min_chain, rec["max_lane_err"],
             rec["actual_span"], rec["chain_span"], rec["model_span"])
        )

    print("\n" + "=" * 78)
    print("SUMMARY PER CHAMBER")
    print("=" * 78)
    print(f"  {'#':<4}{'min_act_gap':>12}{'min_chain_gap':>14}"
          f"{'max_lane_err':>13}{'act_span':>10}{'chain_span':>11}"
          f"{'model_span':>11}{'verdict':>12}")
    n_compressed = 0
    for (idx, mag, mcg, mle, asp, csp, msp) in worst_by_chamber:
        verdict = ""
        if mag < model_gap - COMPRESSION_TOL_M:
            verdict = "COMPRESSED"
            n_compressed += 1
        print(f"  {idx:<4}{mag:>12.2f}{mcg:>14.2f}{mle:>13.2f}"
              f"{asp:>10.2f}{csp:>11.2f}{msp:>11.2f}{verdict:>12}")
    print(f"\nchambers with an actual adjacent gap < "
          f"{model_gap - COMPRESSION_TOL_M:.2f} m : {n_compressed} "
          f"/ {len(records)}")
    print("RESULT: diagnostic complete.")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
