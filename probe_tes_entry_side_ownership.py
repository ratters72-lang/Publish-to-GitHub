"""probe_tes_entry_side_ownership.py — is SIDE / SLOT ownership corrupted at
TES chamber entry?

MOTIVATION
----------
``probe_tes_entry_compression.py`` proved the drive-entry line is laterally
compressed and stacked, and ``verify_tes_entry_lane_geometry.py`` turned that
into an executable failure.  Both measured the *symptom*.  Neither tested the
hypothesis that the underlying cause is SIDE-CLASSIFICATION / SLOT-ASSIGNMENT
corruption at entry — bodies grouped into the wrong side-lane set — nor the
lane-PUBLICATION freeze that decides which lane each body actually holds.

WHAT THIS PROBE MEASURES
------------------------
The un-gated preamble is the single authority that (a) re-splits the line
into LEFT/RIGHT on every frame (``assign_split_sides``) and (b) publishes the
per-body lane dict (``world.frame_spacing_targets``).  During the TES drive
chamber the pipeline early-returns BEFORE republishing, but AFTER the split —
so the two halves of that authority disagree.  At chamber entry this probe
records, per line body and per chamber:

  S1  LANE PUBLICATION FRESHNESS — is the published lane dict the current
      frame's (``frame_geometry_frame == frame_count``), i.e. was the lane
      set the body holds actually computed THIS frame?

  S2  SIDE OWNERSHIP — each body's persistent ``depth_side`` against the side
      implied by its live Y relative to the ruck, and against the side implied
      by the lane the dict published for it.  A body whose ``depth_side``
      disagrees with its own lane is assigned to the WRONG side-lane set.

  S3  SLOT / LANE OVERLAP — the number of distinct line bodies mapped onto a
      shared published lane (|Δy| <= LANE_DUP_M) and the number of line
      bodies absent from the published dict entirely (they then fall back to
      the ruck_y pin — the stack).

  S4  SIDE-COUNT DIVERGENCE — the per-side counts implied by the published
      lanes vs the live per-side count, i.e. whether the count the lanes were
      laid out for still matches the bodies that now hold them.

  S5  INTRA-CHAMBER DRIFT — how many bodies change ``depth_side`` or
      ``depth_role`` while the chamber is live (the split keeps re-running on
      a depleted population).

S1/S2/S4 are the side-classification + lane-publication test; S3 is the slot
overlap test.  Exit 0 always (diagnostic).  Numbers are the deliverable.
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
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_lanes, LEFT, RIGHT,
)

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
LANE_DUP_M = 0.30
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_tes_entry_side_ownership_engine.log"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


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


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _live_side(d, ruck_y):
    return LEFT if float(d.y) < float(ruck_y) else RIGHT


def _chamber_snapshot(world):
    """Build one chamber-entry snapshot (S1..S4)."""
    bodies = line_bodies(world)
    ruck_y = _f(getattr(world, "ruck_y_m", 35.0))
    ruck_x = _f(getattr(world, "ruck_x_m", 60.0))
    frame = int(getattr(world, "frame_count", 0))
    raw_dct = getattr(world, "frame_spacing_targets", None)
    dct = raw_dct if isinstance(raw_dct, dict) else {}
    geo_frame = getattr(world, "frame_geometry_frame", None)
    fresh = (geo_frame == frame)
    ready = bool(getattr(world, "frame_geometry_ready", False))
    dct_type = type(raw_dct).__name__
    dct_len = len(dct)
    n_stale = sum(
        1 for d in bodies
        if getattr(d, "frame_spacing_y", None) is None
    )

    published = []          # (body, published_lane)
    rows = []
    for d in bodies:
        lane = dct.get(id(d))
        stale = getattr(d, "frame_spacing_y", None)
        depth_side = getattr(d, "depth_side", None)
        live = _live_side(d, ruck_y)
        lane_side = None if lane is None else (
            LEFT if float(lane) < ruck_y else RIGHT
        )
        rows.append({
            "pid": getattr(d, "player_id", None),
            "role": _role_name(d),
            "depth_role": getattr(d, "depth_role", None),
            "depth_side": depth_side,
            "live_side": live,
            "y": _f(d.y),
            "lane": None if lane is None else _f(lane),
            "lane_side": lane_side,
            "stale_y": None if stale is None else _f(stale),
        })
        if lane is not None:
            published.append((d, float(lane)))

    # S3 — lane collisions among published lanes.
    published.sort(key=lambda t: t[1])
    dup_pairs = []
    dup_body_ids = set()
    for i in range(len(published) - 1):
        a, ya = published[i]
        b, yb = published[i + 1]
        if abs(yb - ya) <= LANE_DUP_M:
            dup_pairs.append((getattr(a, "player_id", None),
                              getattr(b, "player_id", None), yb - ya))
            dup_body_ids.add(id(a))
            dup_body_ids.add(id(b))
    lanes = [y for _d, y in published]
    distinct_lanes = len(_cluster(lanes, LANE_DUP_M))
    missing = [r["pid"] for r in rows if r["lane"] is None]

    # S2 — ownership disagreements.
    side_mismatch_live = [r["pid"] for r in rows
                          if r["depth_side"] in (-1, 1)
                          and r["depth_side"] != r["live_side"]]
    side_mismatch_lane = [r["pid"] for r in rows
                          if r["depth_side"] in (-1, 1)
                          and r["lane_side"] in (-1, 1)
                          and r["depth_side"] != r["lane_side"]]
    no_side = [r["pid"] for r in rows if r["depth_side"] not in (-1, 1)]

    # S4 — per-side counts: published lane sides vs live body sides.
    lane_left = sum(1 for y in lanes if y < ruck_y)
    lane_right = sum(1 for y in lanes if y >= ruck_y)
    live_left = sum(1 for r in rows if r["live_side"] == LEFT)
    live_right = len(rows) - live_left
    split = getattr(world, "_last_count_split", None)

    span_lanes = (max(lanes) - min(lanes)) if lanes else 0.0
    body_ys = sorted(r["y"] for r in rows)
    span_bodies = (body_ys[-1] - body_ys[0]) if len(body_ys) > 1 else 0.0
    min_body_gap = min(
        (body_ys[i + 1] - body_ys[i] for i in range(len(body_ys) - 1)),
        default=float("inf"),
    )

    return {
        "cid": getattr(world, tds.CHAMBER_ID_ATTR, None),
        "frame": frame,
        "phase": str(getattr(getattr(world, "open_play_engine", None), "_phase", "")),
        "ruck_x": ruck_x,
        "ruck_y": ruck_y,
        "rows": rows,
        "fresh": fresh,
        "geo_frame": geo_frame,
        "ready": ready,
        "dct_type": dct_type,
        "dct_len": dct_len,
        "n_stale": n_stale,
        "n_bodies": len(rows),
        "n_published": len(published),
        "n_distinct_lanes": distinct_lanes,
        "dup_pairs": dup_pairs,
        "n_dup_bodies": len(dup_body_ids),
        "missing": missing,
        "side_mismatch_live": side_mismatch_live,
        "side_mismatch_lane": side_mismatch_lane,
        "no_side": no_side,
        "lane_left": lane_left,
        "lane_right": lane_right,
        "live_left": live_left,
        "live_right": live_right,
        "split": split,
        "span_lanes": span_lanes,
        "span_bodies": span_bodies,
        "min_body_gap": min_body_gap,
    }


def _cluster(values, tol):
    """Group sorted *values* into clusters separated by > tol."""
    out = []
    for v in sorted(values):
        if not out or v - out[-1][-1] > tol:
            out.append([v])
        else:
            out[-1].append(v)
    return out


def main():
    records = []
    flips = {}
    role_moves = {}
    frames_total = 0
    frames_fresh = 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_cid = None
        entry_side = {}
        last_side = {}
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
            if open_play._phase not in DRIVE_PHASES:
                prev_cid = None
                continue

            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if cid != prev_cid:
                prev_cid = cid
                rec = _chamber_snapshot(world)
                records.append(rec)
                entry_side = {
                    r["pid"]: r["depth_side"] for r in rec["rows"]
                }
                last_side = dict(entry_side)
                flips[rec["frame"]] = {}
                role_moves[rec["frame"]] = {}

            # per-frame ownership drift tracking
            frames_total += 1
            if getattr(world, "frame_geometry_frame", None) == world.frame_count:
                frames_fresh += 1
            for d in line_bodies(world):
                pid = getattr(d, "player_id", None)
                if pid not in entry_side:
                    continue
                side = getattr(d, "depth_side", None)
                if side in (-1, 1) and last_side.get(pid) in (-1, 1) \
                        and side != last_side[pid]:
                    key = (last_side[pid], side)
                    bucket = flips.setdefault(prev_cid, {})
                    bucket[key] = bucket.get(key, 0) + 1
                last_side[pid] = side
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    print("=" * 78)
    print("TES CHAMBER-ENTRY SIDE / SLOT OWNERSHIP PROBE")
    print("=" * 78)
    print(f"drive frames observed = {frames_total}; "
          f"frames with a FRESH lane dict = {frames_fresh}")
    if not records:
        print("RESULT: no drive chamber observed.")
        return 0

    print(f"\nchambers observed = {len(records)}")
    print(f"\n{'#':<4}{'cid':>6}{'body':>6}{'pub':>5}{'lane':>6}{'dupP':>6}"
          f"{'dupB':>6}{'miss':>6}{'ds!=live':>10}{'ds!=lane':>10}"
          f"{'noside':>8}{'fresh':>7}")
    for i, rec in enumerate(records, 1):
        print(f"{i:<4}{str(rec['cid']):>6}{rec['n_bodies']:>6}"
              f"{rec['n_published']:>5}{rec['n_distinct_lanes']:>6}"
              f"{len(rec['dup_pairs']):>6}{rec['n_dup_bodies']:>6}"
              f"{len(rec['missing']):>6}{len(rec['side_mismatch_live']):>10}"
              f"{len(rec['side_mismatch_lane']):>10}"
              f"{len(rec['no_side']):>8}{str(rec['fresh']):>7}")

    print("\n" + "=" * 78)
    print("PER-CHAMBER DETAIL")
    print("=" * 78)
    for i, rec in enumerate(records, 1):
        print("\n" + "-" * 78)
        print(f"CHAMBER {i}  cid={rec['cid']}  entry frame={rec['frame']}  "
              f"phase={rec['phase']}")
        print(f"  ruck=({rec['ruck_x']:.2f},{rec['ruck_y']:.2f})  "
              f"lane dict fresh={rec['fresh']} "
              f"(dict frame={rec['geo_frame']}, live frame={rec['frame']})")
        print(f"  S1  frame_geometry_ready={rec['ready']}  "
              f"frame_spacing_targets={rec['dct_type']}"
              f"[{rec['dct_len']}]  bodies with no "
              f"frame_spacing_y stamp: {rec['n_stale']}/{rec['n_bodies']}")
        print(f"  S1  published lanes this frame : {rec['n_published']} "
              f"bodies, {rec['n_distinct_lanes']} distinct lanes")
        print(f"  S3  overlapping-lane bodies    : {rec['n_dup_bodies']} "
              f"({len(rec['dup_pairs'])} adjacent pairs <= {LANE_DUP_M} m)")
        print(f"      bodies absent from ladder  : {rec['missing']}")
        print(f"  S2  depth_side != live side    : {rec['side_mismatch_live']}")
        print(f"      depth_side != lane side    : {rec['side_mismatch_lane']}")
        print(f"      depth_side unset           : {rec['no_side']}")
        print(f"  S4  lane sides L/R             : "
              f"{rec['lane_left']}/{rec['lane_right']}   "
              f"live sides L/R : {rec['live_left']}/{rec['live_right']}   "
              f"split stamp={rec['split']}")
        print(f"      lane span={rec['span_lanes']:.2f}  "
              f"body span={rec['span_bodies']:.2f}  "
              f"min body gap={rec['min_body_gap']:.2f}")
        print(f"  {'pid':<6}{'role':<15}{'depth':<7}{'side':>5}{'live':>5}"
              f"{'lane':>8}{'laneside':>9}{'y':>8}{'stale_y':>9}")
        for r in sorted(rec["rows"], key=lambda r: -r["y"]):
            side_s = {-1: "L", 1: "R"}.get(r["depth_side"], "-")
            live_s = {-1: "L", 1: "R"}.get(r["live_side"], "-")
            lane_s = {-1: "L", 1: "R"}.get(r["lane_side"], "-")
            ln = "-" if r["lane"] is None else f"{r['lane']:.2f}"
            st = "-" if r["stale_y"] is None else f"{r['stale_y']:.2f}"
            print(f"  {str(r['pid']):<6}{str(r['role']):<15}"
                  f"{str(r['depth_role']):<7}{side_s:>5}{live_s:>5}"
                  f"{ln:>8}{lane_s:>9}{r['y']:>8.2f}{st:>9}")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    n_fresh = sum(1 for r in records if r["fresh"])
    n_mis = sum(1 for r in records if r["side_mismatch_live"]
                or r["side_mismatch_lane"] or r["no_side"])
    n_dup = sum(1 for r in records if r["n_dup_bodies"] > 0)
    n_missing = sum(1 for r in records if r["missing"])
    n_countdiv = sum(
        1 for r in records
        if (r["lane_left"], r["lane_right"]) != (r["live_left"], r["live_right"])
    )
    print(f"  chambers with a FRESH lane dict at entry : {n_fresh}/{len(records)}")
    print(f"  chambers with S2 ownership disagreement  : {n_mis}/{len(records)}")
    print(f"  chambers with S3 lane overlap            : {n_dup}/{len(records)}")
    print(f"  chambers with bodies missing from ladder : {n_missing}/{len(records)}")
    print(f"  chambers with S4 count divergence        : {n_countdiv}/{len(records)}")
    total_flips = sum(sum(v.values()) for v in flips.values())
    print(f"  intra-chamber depth_side flips (total)   : {total_flips}")
    for k, v in flips.items():
        if v:
            print(f"    chamber {k}: {v}")
    print("RESULT: diagnostic complete.")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
