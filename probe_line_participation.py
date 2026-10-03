"""probe_line_participation.py — PHASE 1 measurement probe (2026-09-12).

PURPOSE
-------
Measure, live, whether the defensive-line REBUILD consumes the complete
eligible defender population, whether slot OWNERS are silently excluded
from it, and whether any exclusion set GROWS across successive tackles.

The rebuild population is produced by
``defensive_pipeline.run_defensive_pipeline`` -> ``_line_defenders`` ->
``compute_targets`` and published as ``world.frame_spacing_targets`` /
``world.frame_width_targets``.  This probe records, every frame:

  * the FULL defending team,
  * the eligible (non-fullback) subset,
  * the rebuild pool (``_line_defenders``),
  * the keys actually published in both frame dicts,
  * ``frame_spacing_covers`` for the eligible subset,
  * per-body exclusion reason (fullback / tackle_state / spacing-excluded
    role / chase-target field / frozen / none),
  * the TES chamber seq and the slot-ownership table size.

Then it prints, per PHASE BUCKET and per CHAMBER, the bodies that are
eligible but MISSING from the published dict, with their reasons, so the
"trailing non-participating defender" population can be attributed rather
than guessed.

READ-ONLY: the probe never writes to the world.

Run:  python probe_line_participation.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys
from collections import defaultdict, Counter

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

from game.defence.defence_lvl_1.defensive_pipeline import (
    _line_defenders,
    frame_spacing_covers,
    frame_geometry_is_fresh,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    _SPACING_EXCLUDED_ROLES,
)
from game.defence.defence_lvl_1 import tes_drive_structure as tds

MAX_FRAMES = 4000
DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
OUT_LOG = "tmp_probe_line_participation.log"


def _role_name(d):
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def _bucket(world, phase):
    """Coarse phase bucket for aggregation."""
    if phase in DRIVE_PHASES:
        return "TES_DRIVE"
    if phase in ("TACKLE_INIT", "TACKLE_RELEASE"):
        return "TES_EDGE"
    if phase and phase.startswith("PTB"):
        return "PTB"
    if phase == "POST_RUCK_OPEN_PLAY":
        return "POST_RUCK"
    if phase == "OPEN_PLAY":
        return "OPEN_PLAY"
    return phase or "UNKNOWN"


def exclusion_reason(world, d, in_pool, in_spacing, in_width):
    """Why is *d* missing from the published frame dict?"""
    if getattr(d, "fullback_role_active", False):
        return "fullback"
    if getattr(d, "tackle_state_active", False):
        return "tackle_state_active"
    if in_pool and not in_spacing:
        return "in_pool_but_no_spacing_key"
    if not in_pool:
        if _role_name(d) in _SPACING_EXCLUDED_ROLES:
            return "spacing_excluded_role"
        if getattr(d, "tackle_state_active", False):
            return "tackle_state_active"
        return "not_in_rebuild_pool"
    if not in_width:
        return "no_width_key"
    return "ok"


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


def collect():
    """Walk the harness; return (frames, chambers)."""
    frames = []
    chambers = []
    keep = []          # hold every Player alive so id() stays unique
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        keep.extend(world.defending_team)
        dt = 1.0 / 60.0
        prev_cid = None
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
            bucket = _bucket(world, phase)
            team = [p for p in world.defending_team if p is not None]
            eligible = [p for p in team if not getattr(p, "fullback_role_active", False)]
            pool = _line_defenders(list(team))
            spacing = getattr(world, "frame_spacing_targets", None) or {}
            width = getattr(world, "frame_width_targets", None) or {}
            fresh = frame_geometry_is_fresh(world)
            covers = frame_spacing_covers(world, eligible)

            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if bucket == "TES_DRIVE" and cid != prev_cid:
                prev_cid = cid
                ladder = tds.slot_ladder_for(world)
                owners = []
                if isinstance(ladder, dict):
                    for entries in (ladder.get("sides") or {}).values():
                        owners.extend(e.get("owner_id") for e in entries)
                chambers.append({
                    "cid": cid,
                    "frame": int(world.frame_count),
                    "team": sorted(getattr(p, "player_id", 0) for p in team),
                    "eligible": sorted(getattr(p, "player_id", 0) for p in eligible),
                    "pool": sorted(getattr(p, "player_id", 0) for p in pool),
                    "owners": sorted(o for o in owners if o is not None),
                    "ruck_y": float(getattr(world, "ruck_y_m", 0.0) or 0.0),
                })

            rows = []
            for p in team:
                pid = getattr(p, "player_id", None)
                in_pool = p in pool
                in_spacing = id(p) in spacing
                in_width = id(p) in width
                rows.append({
                    "pid": pid,
                    "role": _role_name(p),
                    "depth_role": getattr(p, "depth_role", None),
                    "tackle_state": bool(getattr(p, "tackle_state_active", False)),
                    "frozen": bool(getattr(p, "frozen", False)),
                    "fullback": bool(getattr(p, "fullback_role_active", False)),
                    "in_pool": in_pool,
                    "in_spacing": in_spacing,
                    "in_width": in_width,
                    "slot": tds.owned_slot_y(world, p),
                    "reason": "" if in_spacing else exclusion_reason(
                        world, p, in_pool, in_spacing, in_width
                    ),
                })
            frames.append({
                "frame": int(world.frame_count),
                "phase": phase,
                "bucket": bucket,
                "cid": cid,
                "fresh": fresh,
                "covers": covers,
                "n_team": len(team),
                "n_eligible": len(eligible),
                "n_pool": len(pool),
                "n_spacing": len(spacing),
                "n_width": len(width),
                "rows": rows,
            })
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return frames, chambers


def main():
    frames, chambers = collect()
    print("=" * 78)
    print("LINE PARTICIPATION PROBE")
    print("=" * 78)
    if not frames:
        print("RESULT: no open-play frames observed.")
        return 2

    print("frames observed      : %d" % len(frames))
    print("TES chambers observed: %d" % len(chambers))
    print()

    # ── Per-bucket population summary ──
    by_bucket = defaultdict(list)
    for f in frames:
        by_bucket[f["bucket"]].append(f)

    print("-" * 78)
    print("PER-PHASE-BUCKET POPULATIONS (team / eligible / pool / spacing dict)")
    print("-" * 78)
    print("  %-12s %6s %8s %8s %6s %6s %6s %8s" % (
        "bucket", "frames", "team", "elig", "pool", "sp", "wd", "covers"))
    for bucket, rows in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
        n = len(rows)
        avg = lambda k: sum(r[k] for r in rows) / float(n)
        cov = sum(1 for r in rows if r["covers"]) / float(n)
        print("  %-12s %6d %8.2f %8.2f %6.2f %6.2f %6.2f %8.2f" % (
            bucket, n, avg("n_team"), avg("n_eligible"), avg("n_pool"),
            avg("n_spacing"), avg("n_width"), cov))

    # ── Exclusion reasons ──
    print()
    print("-" * 78)
    print("ELIGIBLE-BODY EXCLUSIONS FROM THE PUBLISHED SPACING DICT")
    print("-" * 78)
    reasons = Counter()
    per_body = Counter()
    for f in frames:
        for r in f["rows"]:
            if r["fullback"] or r["in_spacing"]:
                continue
            reasons[r["reason"]] += 1
            per_body[(r["pid"], r["reason"])] += 1
    if not reasons:
        print("  (none — every eligible body appeared in the spacing dict)")
    for reason, count in reasons.most_common():
        print("  %-26s %8d body-frames" % (reason, count))
    print()
    print("  per-body exclusions (top 20 by frame count):")
    for (pid, reason), count in per_body.most_common(20):
        print("    pid %-5s %-26s %6d frames" % (pid, reason, count))

    # ── Coverage contract ──
    print()
    print("-" * 78)
    print("frame_spacing_covers(world, eligible) — the all-or-nothing gate")
    print("-" * 78)
    total = len(frames)
    true_n = sum(1 for f in frames if f["covers"])
    print("  True : %d / %d frames (%.1f%%)" % (true_n, total, 100.0 * true_n / total))
    print("  False: %d / %d frames (%.1f%%)" % (
        total - true_n, total, 100.0 * (total - true_n) / total))
    # On the False frames, which single reason is common?
    blockers = Counter()
    for f in frames:
        if f["covers"]:
            continue
        for r in f["rows"]:
            if r["fullback"] or r["in_spacing"]:
                continue
            blockers[r["reason"]] += 1
    print("  blocking reasons on False frames:")
    for reason, count in blockers.most_common():
        print("    %-26s %8d" % (reason, count))

    # ── Per-chamber census vs participants ──
    print()
    print("-" * 78)
    print("PER-CHAMBER CENSUS vs REBUILD-POOL PARTICIPATION")
    print("-" * 78)
    print("  %-5s %6s %8s %8s %8s %8s %s" % (
        "cid", "frame", "team", "elig", "pool", "owners", "owners_not_in_pool"))
    for ch in chambers:
        owners = set(ch["owners"])
        pool = set(ch["pool"])
        print("  %-5s %6d %8d %8d %8d %8d %s" % (
            ch["cid"], ch["frame"], len(ch["team"]), len(ch["eligible"]),
            len(ch["pool"]), len(owners), sorted(owners - pool)))

    # ── Growth check across chambers ──
    print()
    print("-" * 78)
    print("GROWTH CHECK — excluded-eligible body count per chamber")
    print("-" * 78)
    growth = []
    for i, ch in enumerate(chambers, 1):
        lo = ch["frame"]
        hi = chambers[i]["frame"] if i < len(chambers) else 10 ** 9
        excl = set()
        for f in frames:
            if not (lo <= f["frame"] < hi):
                continue
            if f["bucket"] != "TES_DRIVE":
                continue
            for r in f["rows"]:
                if r["fullback"] or r["in_spacing"]:
                    continue
                excl.add((r["pid"], r["reason"]))
        growth.append((ch["cid"], len(excl), sorted(p for p, _ in excl)))
        print("  chamber %-4s excluded-eligible bodies: %d  %s" % (
            ch["cid"], len(excl), sorted(p for p, _ in excl)))
    if len(growth) >= 2:
        first = growth[0][1]
        last = growth[-1][1]
        trend = "GROWING" if last > first else (
            "STABLE" if last == first else "SHRINKING")
        print("  trend first->last: %d -> %d  (%s)" % (first, last, trend))
    print()
    print("engine log written to %s" % OUT_LOG)
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
