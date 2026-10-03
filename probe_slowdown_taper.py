"""probe_slowdown_taper.py — measure the pre-tackle slowdown taper across the
committed PRIMARY tackler's final approach, on a REAL kickoff->catch->tackle
cycle (headless).

Run:  python probe_slowdown_taper.py > tmp_slowdown_taper.txt 2>&1

WHY
---
Fix D (2026-09-15) makes `anticipation_slowdown_point` return the LIVE ball
carrier as the slowdown anchor whenever the anticipation layer is disabled
(its shipped state after the even-ladder rework).  This probe answers:

  1. On the real approach, what taper does the OLD anchor produce?
     (anchor = bc + bc.v * anticipation_lead_time_s == "bc_predicted")
  2. What taper does the NEW anchor produce?  (anchor = live bc == "bc_live")
  3. What did the ENGINE actually apply (d.move_speed), and did it equal 2?

Both anchors are evaluated on the SAME recorded trajectory, so the comparison
isolates the anchor change and nothing else.

The taper formula is the one both consumers use
(`intercept_tacklers.apply_pre_tackle_stance` and
`pre_tackle_authority` COMMIT branch):

    t    = clamp((anchor_dist - plant_r) / (band - plant_r), 0, 1)
    cap  = speed * (scale + (1 - scale) * t)

where `speed` is the tackler's uncapped commit speed and `scale` the floor.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys
import math

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame
pygame.init()
pygame.display.set_mode((1, 1))

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

from game.defence.defence_lvl_1.anticipation_layer import (
    anticipation_slowdown_point,
)
# SPEED-AUTHORITY CONSOLIDATION (2026-09-15): report whether each recorded
# approach frame was actually OWNED by the pre-tackle corridor, so the
# taper comparison below is read against the real writer -- a frame that
# was not corridor-owned cannot be evidence that the taper acted.
from game.defence.defence_lvl_1 import pretackle_corridor as _C

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
FPS = 60
MAX_FRAMES = 1500
APPROACH_M = 6.0        # record the approach from this distance inward


def build():
    field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.vel = (0.0, 0.0)
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, open_play, kickoff_engine, collision_system, ball


def main():
    world, open_play, kickoff_engine, collision_system, ball = build()
    tuners = getattr(open_play, "def_tuners", None)
    dt = 1.0 / FPS

    lead = float(getattr(tuners, "anticipation_lead_time_s", 0.35))
    band = float(getattr(world, "pre_tackle_slowdown_band_m",
                         getattr(tuners, "pre_tackle_slowdown_band_m", 4.0)))
    scale = float(getattr(world, "pre_tackle_slowdown_scale",
                          getattr(tuners, "pre_tackle_slowdown_scale", 0.32)))
    plant_r = float(getattr(tuners, "pretackle_plant_radius_m", 0.8))
    stance_r = float(getattr(world, "pre_tackle_stance_radius_m", 0.8))

    print("=== TAPER PARAMETERS (live world/tuners) ===")
    print(f"  anticipation_enabled        = "
          f"{getattr(tuners, 'anticipation_enabled', None)}")
    print(f"  anticipation_slowdown_anchor= "
          f"{getattr(world, 'pre_tackle_slowdown_anchor', None)}")
    print(f"  anticipation_lead_time_s    = {lead}")
    print(f"  slowdown band (m)           = {band}")
    print(f"  slowdown scale (floor)      = {scale}")
    print(f"  plant radius (m)            = {plant_r}")
    print(f"  stance radius (m)           = {stance_r}")

    rows = []          # one row per recorded approach frame
    anchored = set()   # primary ids we have started recording

    for i in range(MAX_FRAMES):
        ball.update(dt)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None and world.open_play is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            continue

        for d in world.defending_team:
            if getattr(d, "tackle_role", None) != "PRIMARY":
                continue
            dist = math.hypot(d.x - float(bc.x), d.y - float(bc.y))
            if dist > APPROACH_M:
                continue
            pid = getattr(d, "player_id", id(d))

            # OLD anchor (bc_predicted) computed inline from the same BC.
            old_ax = float(bc.x) + float(getattr(bc, "vx", 0.0)) * lead
            old_ay = float(bc.y) + float(getattr(bc, "vy", 0.0)) * lead
            old_dist = math.hypot(d.x - old_ax, d.y - old_ay)

            # NEW anchor — the actual function (post-fix, gate-driven).
            new_ax, new_ay = anticipation_slowdown_point(world, bc, tuners, d)
            new_dist = math.hypot(d.x - new_ax, d.y - new_ay)

            def _cap(anchor_dist, commit_speed):
                if band <= plant_r:
                    return commit_speed
                if anchor_dist < band:
                    t = (anchor_dist - plant_r) / (band - plant_r)
                    t = max(0.0, min(1.0, t))
                    return commit_speed * (scale + (1.0 - scale) * t)
                return commit_speed

            commit_speed = float(getattr(tuners, "pretackle_commit_speed_mps",
                                         4.0))
            rows.append({
                "frame": i,
                "id": pid,
                "dist": dist,
                "old_dist": old_dist,
                "new_dist": new_dist,
                "old_cap": _cap(old_dist, commit_speed),
                "new_cap": _cap(new_dist, commit_speed),
                "move_speed": float(getattr(d, "move_speed", 0.0) or 0.0),
                "speed": math.hypot(float(getattr(d, "vx", 0.0) or 0.0),
                                    float(getattr(d, "vy", 0.0) or 0.0)),
                "commit_speed": commit_speed,
                "src": getattr(d, "target_source", ""),
                "managed": getattr(d, "_pretackle_attempt_state", None),
                "phase": str(getattr(world, "sub_state", "")),
                # CORRIDOR OWNERSHIP: the real speed-authority evidence.
                "owned": bool(_C.is_corridor_owned(d)),
                "frames": int(_C.corridor_frames(d)),
            })
            anchored.add(pid)

    if not rows:
        print("\n!! No PRIMARY tackler ever entered the approach window — "
              "cannot measure. (Increase MAX_FRAMES or inspect the cycle.)")
        pygame.quit()
        return

    # ── Pick the most-sampled primary as the representative approach ──
    counts = {}
    for r in rows:
        counts[r["id"]] = counts.get(r["id"], 0) + 1
    rep = max(counts, key=lambda k: counts[k])
    trace = [r for r in rows if r["id"] == rep]

    print(f"\n=== REPRESENTATIVE APPROACH (primary player_id={rep}, "
          f"{len(trace)} frames) ===")
    print(f"{'frame':>6} {'dist':>6} {'old_d':>6} {'new_d':>6} "
          f"{'old_cap':>8} {'new_cap':>8} {'mv_spd':>7} {'v':>6} "
          f"{'own':>4} {'cf':>4} {'src':<18}")
    for r in trace[:80]:
        print(f"{r['frame']:>6} {r['dist']:>6.2f} {r['old_dist']:>6.2f} "
              f"{r['new_dist']:>6.2f} {r['old_cap']:>8.2f} "
              f"{r['new_cap']:>8.2f} {r['move_speed']:>7.2f} "
              f"{r['speed']:>6.2f} {str(r['owned'])[0]:>4} "
              f"{r['frames']:>4} {r['src'][:18]:<18}")
    if len(trace) > 80:
        print(f"  ... ({len(trace) - 80} more frames)")

    # ── Summary: how much of the approach is inside the taper? ──
    def _summary(key):
        engaged = [r for r in rows if r[key] < band and r["dist"] > plant_r]
        if not engaged:
            return (0, 0.0, 0.0)
        inner = [r for r in rows if r[key] < plant_r]
        caps = [r[key.replace("_dist", "_cap")] for r in engaged]
        outermost = max(r[key] for r in engaged)
        return (len(engaged), outermost, min(caps))

    print("\n=== TAPER ENGAGEMENT ACROSS ALL PRIMARY APPROACH FRAMES ===")
    for label, key, capkey in (
        ("OLD anchor (bc_predicted, pre-fix)", "old_dist", "old_cap"),
        ("NEW anchor (live BC, post-fix)    ", "new_dist", "new_cap"),
    ):
        engaged = [r for r in rows if r[key] < band and r["dist"] > plant_r]
        caps = [r[capkey] for r in engaged]
        start = max((r[key] for r in engaged), default=0.0)
        print(f"  {label}: engaged_frames={len(engaged):>5}  "
              f"taper starts at anchor_dist={start:>5.2f} m  "
              f"min_cap={min(caps) if caps else 0.0:>5.2f} m/s  "
              f"(ceiling {rows[0]['commit_speed']:.2f})")

    # Effective taper WINDOW in live-BC distance terms.
    for label, key in (("OLD", "old_dist"), ("NEW", "new_dist")):
        engaged = [r for r in rows if r[key] < band and r["dist"] > plant_r]
        if engaged:
            widest = max(r["dist"] for r in engaged)
            print(f"  {label} estimated live-BC taper window: "
                  f"{plant_r:.2f} m -> {widest:.2f} m "
                  f"({widest - plant_r:.2f} m of approach)")

    # ── Ground truth: did the engine honour the NEW cap? ──
    print("\n=== GROUND TRUTH (engine) ===")
    managed_rows = [r for r in rows if r["managed"]]
    print(f"  approach frames                      : {len(rows)}")
    print(f"  frames owned by pre-tackle authority : {len(managed_rows)}")
    if managed_rows:
        worst = max(abs(r["move_speed"] - r["new_cap"]) for r in managed_rows)
        worst_old = max(abs(r["move_speed"] - r["old_cap"])
                        for r in managed_rows)
        print(f"  max |move_speed - NEW_cap| (managed) : {worst:.3f} m/s")
        print(f"  max |move_speed - OLD_cap| (managed) : {worst_old:.3f} m/s")
    unmanaged = [r for r in rows if not r["managed"]]
    if unmanaged:
        worst_u = min(abs(r["move_speed"] - r["new_cap"]) for r in unmanaged)
        print(f"  min |move_speed - NEW_cap| (unmanaged, stance taper): "
              f"{worst_u:.3f} m/s")

    # ── Corridor ownership: is the taper's writer the FINAL writer? ──
    owned_rows = [r for r in rows if r["owned"]]
    print(f"\n=== CORRIDOR OWNERSHIP (the taper's real authority) ===")
    print(f"  approach frames recorded             : {len(rows)}")
    print(f"  corridor-owned frames                : {len(owned_rows)}")
    if owned_rows:
        streaks = sorted({r["frames"] for r in owned_rows})
        print(f"  corridor streak range (frames)       : "
              f"{streaks[0]} .. {streaks[-1]}")
        inner = [r for r in owned_rows if r["dist"] <= r.get("band", 4.0)]
        print(f"  corridor-owned AND inside the band   : {len(inner)}")
    tail_owned = [r for r in rows if r["owned"]]
    if tail_owned:
        deepest = min(tail_owned, key=lambda r: r["dist"])
        print(f"  deepest corridor-owned frame         : "
              f"{deepest['dist']:.2f} m from BC "
              f"(src={deepest['src']!r})")
    else:
        print("  !! NO approach frame was corridor-owned — the taper's "
              "writer never held authority; any tuning here is blind.")

    deltas = [r["old_cap"] - r["new_cap"] for r in rows]
    print(f"\n  mean cap reduction (old->new)        : "
          f"{sum(deltas) / len(deltas):.3f} m/s")
    print(f"  max  cap reduction (old->new)        : {max(deltas):.3f} m/s")

    pygame.quit()


if __name__ == "__main__":
    main()
